import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .client import GitHubClient, RateLimitedError
from .storage import Storage

log = logging.getLogger("crawler")

EARLIEST = int(datetime(2007, 10, 1, tzinfo=timezone.utc).timestamp())
INDEX_LAG = 300
GAP_MIN = 3600
SEARCH_CAP = 1000
PER_PAGE = 100
DEFAULT_SPAN = 7 * 86400
MAX_SPAN = 2 * 365 * 86400


@dataclass(frozen=True)
class Tier:
    lo: int
    hi: int | None

    @property
    def key(self) -> str:
        return f"tier:{self.lo}+" if self.hi is None else f"tier:{self.lo}-{self.hi}"

    @property
    def query(self) -> str:
        if self.hi is None:
            return f"stars:>={self.lo}"
        return f"stars:{self.lo}" if self.lo == self.hi else f"stars:{self.lo}..{self.hi}"


DEFAULT_TIERS = (Tier(1000, None), Tier(100, 999), Tier(10, 99), Tier(1, 9), Tier(0, 0))


@dataclass
class Config:
    token: str
    db_path: str = "crawler.db"
    duration: float | None = None
    workers: int = 8
    reserve_ratio: float = 0.01
    max_backlog: int = 50_000
    tiers: tuple[Tier, ...] = field(default=DEFAULT_TIERS)
    report_every: float = 60


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Crawler:
    def __init__(self, cfg: Config, client: GitHubClient | None = None, storage: Storage | None = None):
        self.cfg = cfg
        self.client = client or GitHubClient(cfg.token, cfg.reserve_ratio)
        self.storage = storage or Storage(cfg.db_path)
        self.stop = asyncio.Event()
        self.new_repos = 0
        self.readmes = 0

    # ---- discovery (search bucket) ----

    async def search(self, q: str, page: int) -> dict:
        params = {"q": q, "sort": "stars", "order": "desc", "per_page": PER_PAGE, "page": page}
        for _ in range(3):
            r = await self.client.get("search", "/search/repositories", params=params)
            r.raise_for_status()
            data = r.json()
            if not data.get("incomplete_results"):
                break
        return data

    async def scan(self, tier: Tier, state: dict, end: int, floor: int) -> int:
        """Fetch every repo of `tier` created in [start, end); returns start."""
        span = state.get("span", DEFAULT_SPAN)
        start = max(end - span, floor)
        while True:
            q = f"{tier.query} created:{iso(start)}..{iso(end - 1)} fork:true"
            first = await self.search(q, 1)
            total = first["total_count"]
            if total <= SEARCH_CAP or end - start <= 1:
                break
            start = end - max(1, (end - start) // 2)
        if total > SEARCH_CAP:
            log.warning("%s: %d repos in 1s at %s, truncated to %d", tier.key, total, iso(start), SEARCH_CAP)

        self.new_repos += self.storage.upsert_repos(first["items"])
        for page in range(2, math.ceil(min(total, SEARCH_CAP) / PER_PAGE) + 1):
            self.new_repos += self.storage.upsert_repos((await self.search(q, page))["items"])

        width = end - start
        state["span"] = min(width * 2, MAX_SPAN) if total < SEARCH_CAP // 4 else width
        log.info("%s %s..%s: %d repos", tier.key, iso(start), iso(end), total)
        return start

    async def step(self) -> bool:
        """Advance the highest-priority unfinished cursor by one window. False when all caught up."""
        now = int(time.time()) - INDEX_LAG
        for tier in self.cfg.tiers:
            state = self.storage.get_state(tier.key) or {"upper": now, "lower": now}
            if "gap_top" not in state and now - state["upper"] >= GAP_MIN:
                state["gap_top"] = state["gap_cursor"] = now
            if "gap_top" in state:
                state["gap_cursor"] = await self.scan(tier, state, state["gap_cursor"], state["upper"])
                if state["gap_cursor"] <= state["upper"]:
                    state["upper"] = state.pop("gap_top")
                    del state["gap_cursor"]
                self.storage.set_state(tier.key, state)
                return True
            if state["lower"] > EARLIEST:
                state["lower"] = await self.scan(tier, state, state["lower"], EARLIEST)
                self.storage.set_state(tier.key, state)
                return True
            self.storage.set_state(tier.key, state)
        return False

    async def discover(self) -> None:
        while not self.stop.is_set():
            if self.storage.pending_count() >= self.cfg.max_backlog:
                await asyncio.sleep(30)
                continue
            try:
                if not await self.step():
                    await asyncio.sleep(60)
            except RateLimitedError as e:
                log.warning("%s", e)
                await asyncio.sleep(60)

    # ---- README (core bucket) ----

    async def feed(self, queue: asyncio.Queue) -> None:
        while not self.stop.is_set():
            if queue.qsize() >= self.cfg.workers:
                await asyncio.sleep(0.5)
                continue
            batch = self.storage.claim_readmes(self.cfg.workers * 2)
            if not batch:
                await asyncio.sleep(5)
            for item in batch:
                await queue.put(item)

    async def fetch_readmes(self, queue: asyncio.Queue) -> None:
        while not self.stop.is_set():
            repo_id, full_name = await queue.get()
            try:
                r = await self.client.get(
                    "core", f"/repos/{full_name}/readme", headers={"Accept": "application/vnd.github.raw+json"}
                )
            except RateLimitedError as e:
                log.warning("%s", e)
                self.storage.set_readme(repo_id, "pending", None)
                continue
            if r.status_code == 200:
                self.storage.set_readme(repo_id, "ok", r.text)
                self.readmes += 1
            elif r.status_code == 404:
                self.storage.set_readme(repo_id, "none", None)
            else:
                log.warning("README %s: HTTP %s", full_name, r.status_code)
                self.storage.set_readme(repo_id, "error", None)

    async def report(self) -> None:
        last = {n: l.sent for n, l in self.client.limiters.items()}
        while True:
            await asyncio.sleep(self.cfg.report_every)
            rates = []
            for name, lim in self.client.limiters.items():
                rate = (lim.sent - last[name]) * 60 / self.cfg.report_every
                last[name] = lim.sent
                rates.append(f"{name} {rate:.1f}/min (remaining {lim.remaining})")
            log.info("%s | new repos %d, readmes %d, backlog %d",
                     ", ".join(rates), self.new_repos, self.readmes, self.storage.pending_count())

    # ---- lifecycle ----

    async def run(self) -> dict:
        self.storage.release_queued()
        await self.client.init_limits()
        queue: asyncio.Queue = asyncio.Queue()
        tasks = [
            asyncio.create_task(self.discover()),
            asyncio.create_task(self.feed(queue)),
            asyncio.create_task(self.report()),
            *(asyncio.create_task(self.fetch_readmes(queue)) for _ in range(self.cfg.workers)),
        ]
        stopper = asyncio.create_task(self.stop.wait())
        started = time.time()
        try:
            done, _ = await asyncio.wait([*tasks, stopper], timeout=self.cfg.duration,
                                         return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t is not stopper and t.exception():
                    raise t.exception()
        finally:
            self.stop.set()
            for t in [*tasks, stopper]:
                t.cancel()
            await asyncio.gather(*tasks, stopper, return_exceptions=True)
            self.storage.release_queued()
            await self.client.close()
            summary = {
                "seconds": round(time.time() - started),
                "new_repos": self.new_repos,
                "readmes": self.readmes,
                "requests": {n: l.sent for n, l in self.client.limiters.items()},
            }
            log.info("session summary: %s", summary)
        return summary
