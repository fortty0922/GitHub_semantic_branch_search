import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .client import GitHubClient, GraphQLError, RateLimitedError
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
    graphql_workers: int = 4
    batch_size: int = 50
    reserve_ratio: float = 0.01
    max_backlog: int = 50_000
    tiers: tuple[Tier, ...] = field(default=DEFAULT_TIERS)
    report_every: float = 60


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_query(tier: Tier, start: int, end: int) -> str:
    return f"{tier.query} created:{iso(start)}..{iso(end - 1)} fork:true"


MIN_BATCH = 5
README_NAME = re.compile(r"^readme(\.[\w-]+)*$", re.I)
README_EXT_RANK = (".md", ".markdown", ".rst", ".txt", ".adoc", ".org", "")


def blob_field(path: str) -> str:
    return f"f: object(expression: {json.dumps('HEAD:' + path)}) {{ ... on Blob {{ text }} }}"


ROOT_FIELDS = (
    'md: object(expression: "HEAD:README.md") { ... on Blob { text } } '
    'root: object(expression: "HEAD:") { ... on Tree { entries { name type } } }'
)


def build_query(repos: dict[str, tuple[str, str]]) -> str:
    parts = []
    for alias, (full_name, fields) in repos.items():
        owner, name = full_name.split("/", 1)
        parts.append(f"{alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {fields} }}")
    return "query { " + " ".join(parts) + " }"


def classify(repo: dict | None) -> tuple[str, str | None]:
    """Map a GraphQL repository result to (status, text-or-path). status: ok | none | rest | fetch."""
    if repo is None:
        return "rest", None
    if (repo.get("md") or {}).get("text") is not None:
        return "ok", repo["md"]["text"]
    root = repo.get("root")
    if root is None:
        return "none", None
    entries = root.get("entries") or []
    names = [e["name"] for e in entries if e["type"] == "blob" and README_NAME.match(e["name"]) and e["name"] != "README.md"]
    if names:
        def rank(n: str) -> int:
            ext = n[6:].lower()
            return README_EXT_RANK.index(ext) if ext in README_EXT_RANK else len(README_EXT_RANK)
        return "fetch", min(names, key=rank)
    if any(e["type"] == "tree" and e["name"].lower() in (".github", "docs") for e in entries):
        return "rest", None
    return "none", None


class Crawler:
    def __init__(self, cfg: Config, client: GitHubClient | None = None, storage: Storage | None = None):
        self.cfg = cfg
        self.client = client or GitHubClient(cfg.token, cfg.reserve_ratio)
        self.storage = storage or Storage(cfg.db_path)
        self.stop = asyncio.Event()
        self.new_repos = 0
        self.readmes = 0
        self.batch_size = cfg.batch_size
        self._prefetch: dict[str, asyncio.Task] = {}

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

    async def first_page(self, q: str) -> dict:
        task = self._prefetch.pop(q, None)
        for stale in self._prefetch.values():
            stale.cancel()
        self._prefetch.clear()
        return await task if task else await self.search(q, 1)

    def prefetch(self, q: str) -> None:
        task = asyncio.create_task(self.search(q, 1))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        self._prefetch[q] = task

    async def scan(self, tier: Tier, state: dict, end: int, floor: int) -> int:
        """Fetch every repo of `tier` created in [start, end); returns start.

        Pages 2..N are requested concurrently and the next window's first page is
        prefetched, so search latency doesn't throttle the request rate.
        """
        span = state.get("span", DEFAULT_SPAN)
        start = max(end - span, floor)
        while True:
            q = window_query(tier, start, end)
            first = await self.first_page(q)
            total = first["total_count"]
            if total <= SEARCH_CAP or end - start <= 1:
                break
            start = end - max(1, (end - start) // 2)
        if total > SEARCH_CAP:
            log.warning("%s: %d repos in 1s at %s, truncated to %d", tier.key, total, iso(start), SEARCH_CAP)

        width = end - start
        state["span"] = min(width * 2, MAX_SPAN) if total < SEARCH_CAP // 4 else width
        if start > floor:
            self.prefetch(window_query(tier, max(start - state["span"], floor), start))

        pages = range(2, math.ceil(min(total, SEARCH_CAP) / PER_PAGE) + 1)
        results = [first, *await asyncio.gather(*(self.search(q, p) for p in pages))]
        for page in results:
            self.new_repos += self.storage.upsert_repos(page["items"])
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
            except (RateLimitedError, ValueError, httpx.TransportError) as e:
                log.warning("discovery: %s", e)
                await asyncio.sleep(60)

    # ---- README via GraphQL (graphql bucket) ----

    async def graphql_worker(self) -> None:
        while not self.stop.is_set():
            rows = self.storage.claim_readmes(self.batch_size)
            if not rows:
                await asyncio.sleep(5)
                continue
            try:
                await self.graphql_readmes(rows)
            except RateLimitedError as e:
                log.warning("%s", e)
                self.storage.requeue([r[0] for r in rows], "pending")
            except Exception:
                log.exception("GraphQL README batch failed; handing %d repos to REST", len(rows))
                self.storage.requeue([r[0] for r in rows], "rest")

    async def graphql_readmes(self, rows: list[tuple[int, str]]) -> None:
        try:
            data = await self.client.graphql(build_query({f"r{i}": (n, ROOT_FIELDS) for i, (_, n) in enumerate(rows)}))
        except GraphQLError as e:
            if len(rows) == 1:
                self.storage.set_readme(rows[0][0], "rest", None)
                return
            self.batch_size = max(MIN_BATCH, len(rows) // 2)
            log.info("GraphQL batch of %d failed (%s), splitting", len(rows), e)
            half = len(rows) // 2
            await self.graphql_readmes(rows[:half])
            await self.graphql_readmes(rows[half:])
            return
        self.batch_size = min(self.cfg.batch_size, self.batch_size + 1)

        follow = {}
        for i, (repo_id, full_name) in enumerate(rows):
            status, value = classify(data.get(f"r{i}"))
            if status == "fetch":
                follow[f"r{i}"] = (repo_id, full_name, value)
            else:
                self._save_readme(repo_id, status, value)
        if not follow:
            return
        query = build_query({a: (n, blob_field(path)) for a, (_, n, path) in follow.items()})
        try:
            data = await self.client.graphql(query)
        except GraphQLError:
            data = {}
        for alias, (repo_id, _, _) in follow.items():
            blob = ((data.get(alias) or {}).get("f") or {})
            text = blob.get("text")
            self._save_readme(repo_id, "ok" if text is not None else "rest", text)

    def _save_readme(self, repo_id: int, status: str, text: str | None) -> None:
        self.storage.set_readme(repo_id, status, text)
        if status == "ok":
            self.readmes += 1

    # ---- README via REST (core bucket): GraphQL misses first, then regular pending ----

    async def feed(self, queue: asyncio.Queue) -> None:
        while not self.stop.is_set():
            if queue.qsize() >= self.cfg.workers:
                await asyncio.sleep(0.5)
                continue
            want = self.cfg.workers * 2
            batch = self.storage.claim_readmes(want, status="rest")
            batch += self.storage.claim_readmes(want - len(batch))
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
            except Exception:
                log.exception("README %s failed", full_name)
                self.storage.set_readme(repo_id, "error", None)
                continue
            if r.status_code == 200:
                self._save_readme(repo_id, "ok", r.text)
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
            *(asyncio.create_task(self.graphql_worker()) for _ in range(self.cfg.graphql_workers)),
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
            pending = [*tasks, stopper, *self._prefetch.values()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
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
