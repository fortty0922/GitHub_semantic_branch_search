import asyncio
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .client import GitHubClient, GraphQLError, RateLimitedError
from .storage import Storage

log = logging.getLogger("crawler")
SUMMARY = {"summary": True}

EARLIEST = int(datetime(2007, 10, 1, tzinfo=timezone.utc).timestamp())
INDEX_LAG = 300
GAP_MIN = 3600
PAUSE_CHECK = 10
BATCH_KEY = "graphql_batch_max"
BATCH_PROBE_EVERY = 100
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
    graphql_workers: int = 6
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
        self.batch_max = cfg.batch_size
        self.events: Counter = Counter()
        self.paused_since: float | None = None
        self.paused_total = 0.0
        self.graphql_streak = 0
        self._prefetch: dict[tuple, tuple[asyncio.Task, asyncio.Future]] = {}

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

    async def fetch_window(self, tier: Tier, end: int, floor: int, span: int, bounds: asyncio.Future) -> list[dict]:
        """Probe/split until the window fits the search cap, publish (start, total) on `bounds`, then fetch all pages."""
        start = max(end - span, floor)
        while True:
            q = window_query(tier, start, end)
            first = await self.search(q, 1)
            total = first["total_count"]
            if total <= SEARCH_CAP or end - start <= 1:
                break
            start = end - max(1, (end - start) // 2)
        bounds.set_result((start, total))
        pages = range(2, math.ceil(min(total, SEARCH_CAP) / PER_PAGE) + 1)
        return [first, *await asyncio.gather(*(self.search(q, p) for p in pages))]

    def window_task(self, tier: Tier, end: int, floor: int, span: int) -> tuple[asyncio.Task, asyncio.Future]:
        bounds = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self.fetch_window(tier, end, floor, span, bounds))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return task, bounds

    def drop_prefetch(self) -> None:
        for task, _ in self._prefetch.values():
            task.cancel()
        self._prefetch.clear()

    async def scan(self, tier: Tier, state: dict, end: int, floor: int) -> int:
        """Fetch every repo of `tier` created in [start, end); returns start.

        Windows are pipelined: as soon as this window's bounds are known, the whole next
        window (probe and pages) starts in the background, so search latency doesn't
        throttle the request rate. Results are still committed in window order.
        """
        task, bounds = self._prefetch.pop((tier.key, end, floor), None) or             self.window_task(tier, end, floor, state.get("span", DEFAULT_SPAN))
        self.drop_prefetch()
        await asyncio.wait([task, bounds], return_when=asyncio.FIRST_COMPLETED)
        if not bounds.done():
            task.result()
        start, total = bounds.result()
        if total > SEARCH_CAP:
            self.events["truncated_windows"] += 1
            log.warning("%s: %d repos in 1s at %s, truncated to %d", tier.key, total, iso(start), SEARCH_CAP)

        width = end - start
        state["span"] = min(width * 2, MAX_SPAN) if total < SEARCH_CAP // 4 else width
        if start > floor:
            self._prefetch[(tier.key, start, floor)] = self.window_task(tier, start, floor, state["span"])

        for page in await task:
            self.new_repos += self.storage.upsert_repos(page["items"])
        self.events["windows"] += 1
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
            backlog = self.storage.pending_count()
            if backlog >= self.cfg.max_backlog:
                if self.paused_since is None:
                    self.paused_since = time.time()
                    self.events["discovery_pauses"] += 1
                    log.info("discovery paused: backlog %d >= %d", backlog, self.cfg.max_backlog, extra=SUMMARY)
                await asyncio.sleep(PAUSE_CHECK)
                continue
            if self.paused_since is not None:
                paused = time.time() - self.paused_since
                self.paused_total += paused
                self.paused_since = None
                log.info("discovery resumed after %.0fs: backlog %d", paused, backlog, extra=SUMMARY)
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
            self.set_batch_max(min(self.batch_max, int(len(rows) * 0.8)))
            self.graphql_streak = 0
            self.batch_size = max(MIN_BATCH, len(rows) // 2)
            self.events["graphql_splits"] += 1
            log.info("GraphQL batch of %d failed (%s), splitting", len(rows), e, extra=SUMMARY)
            half = len(rows) // 2
            await self.graphql_readmes(rows[:half])
            await self.graphql_readmes(rows[half:])
            return
        self.graphql_streak += 1
        if self.graphql_streak % BATCH_PROBE_EVERY == 0 and self.batch_max < self.cfg.batch_size:
            self.set_batch_max(self.batch_max + 1)
        self.batch_size = min(self.batch_max, self.batch_size + 1)

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

    def load_batch_max(self) -> None:
        saved = self.storage.get_state(BATCH_KEY)
        if saved:
            self.batch_max = max(MIN_BATCH, min(int(saved), self.cfg.batch_size))
            self.batch_size = self.batch_max

    def set_batch_max(self, value: int) -> None:
        value = max(MIN_BATCH, min(value, self.cfg.batch_size))
        if value != self.batch_max:
            self.batch_max = value
            self.storage.set_state(BATCH_KEY, value)

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
                self.events[f"readme_http_{r.status_code}"] += 1
                log.info("README %s: HTTP %s", full_name, r.status_code)
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
            log.info("%s | new repos %d, readmes %d, backlog %d%s",
                     ", ".join(rates), self.new_repos, self.readmes, self.storage.pending_count(),
                     " | discovery paused" if self.paused_since is not None else "")

    # ---- lifecycle ----

    async def run(self) -> dict:
        self.storage.release_queued()
        self.load_batch_max()
        await self.client.init_limits()
        log.info("session start: duration=%ss workers=%d graphql_workers=%d batch=%d (max %d) "
                 "max_backlog=%d db_repos=%d quotas=%s",
                 self.cfg.duration, self.cfg.workers, self.cfg.graphql_workers, self.batch_size, self.batch_max,
                 self.cfg.max_backlog,
                 self.storage.stats()["repos"],
                 {n: l.remaining for n, l in self.client.limiters.items()}, extra=SUMMARY)
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
            self.client.closing = True
            pending = [*tasks, stopper, *(t for t, _ in self._prefetch.values())]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.storage.release_queued()
            await self.client.close()
            seconds = max(time.time() - started, 1)
            db = self.storage.stats()
            summary = {
                "seconds": round(seconds),
                "new_repos": self.new_repos,
                "readmes": self.readmes,
                "requests": {n: l.sent for n, l in self.client.limiters.items()},
                "per_min": {n: round(l.sent * 60 / seconds, 1) for n, l in self.client.limiters.items()},
                "events": dict(self.events + self.client.events),
                "discovery_paused_s": round(self.paused_total + (
                    time.time() - self.paused_since if self.paused_since is not None else 0)),
                "graphql_batch_max": self.batch_max,
                **self.client.metrics(),
                "db_repos": db["repos"],
                "db_readme": db["readme"],
                "cursors": {k: {f: iso(v) if f in ("upper", "lower") else v for f, v in s.items()}
                            for k, s in db["state"].items() if isinstance(s, dict)},
            }
            log.info("session summary: %s", json.dumps(summary, ensure_ascii=False), extra=SUMMARY)
        return summary
