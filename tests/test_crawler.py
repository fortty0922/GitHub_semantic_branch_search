import asyncio
import itertools
import json
import re
import time
from datetime import datetime

import httpx
import pytest

from crawler import crawl
from crawler.client import BucketLimiter, GitHubClient
from crawler.crawl import Config, Crawler, Tier
from crawler.storage import Storage

RANGE = re.compile(r"created:(\S+)\.\.(\S+)")


def ts(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=crawl.timezone.utc).timestamp())


def make_crawler(tmp_path, handler, tiers=crawl.DEFAULT_TIERS, **cfg):
    client = GitHubClient("t", transport=httpx.MockTransport(handler), diag_dir=tmp_path / "diag")
    for lim in client.limiters.values():
        lim.limit = lim.remaining = 10**9
    return Crawler(Config(token="t", tiers=tiers, **cfg), client=client, storage=Storage(str(tmp_path / "db.sqlite")))


def empty_search(log):
    def handler(request):
        log.append(request.url.params["q"])
        return httpx.Response(200, json={"total_count": 0, "incomplete_results": False, "items": []})
    return handler


def repo(i, stars=0):
    return {"id": i, "full_name": f"o/r{i}", "stargazers_count": stars, "created_at": "2024-01-01T00:00:00Z"}


async def drain(c, limit=500):
    for _ in range(limit):
        if not await c.step():
            return
    raise AssertionError("crawler never caught up")


def test_limiter_paces_evenly_with_reserve():
    lim = BucketLimiter("core", 5000, 3600, reserve_ratio=0.01)
    now = time.time()
    lim.update(remaining=5000, reset=now + 3600)
    assert lim.interval(now) == pytest.approx(3600 / 4950, rel=1e-3)
    lim.remaining = 50
    assert lim.interval(now) > 3600


def test_limiter_rolls_over_after_reset():
    lim = BucketLimiter("search", 30, 60)
    lim.remaining, lim.reset = 0, time.time() - 1
    assert lim.interval(time.time()) == pytest.approx(60 / 29, rel=1e-2)
    assert lim.remaining == 30


async def test_retry_after_429():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={})

    client = GitHubClient("t", transport=httpx.MockTransport(handler))
    r = await client.get("core", "/x")
    assert r.status_code == 200 and len(calls) == 2


def test_upsert_keeps_readme_and_dedupes(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    assert s.upsert_repos([repo(1, 5), repo(2)]) == 2
    s.set_readme(1, "ok", "hello")
    assert s.upsert_repos([repo(1, 99)]) == 0
    row = s.db.execute("SELECT stars, readme_status, readme FROM repos WHERE id = 1").fetchone()
    assert row == (99, "ok", "hello")
    assert s.stats()["repos"] == 2


def test_claim_orders_by_stars(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    s.upsert_repos([repo(1, 1), repo(2, 50), repo(3, 10)])
    assert [r[0] for r in s.claim_readmes(2)] == [2, 3]
    s.release_queued()
    assert s.stats()["readme"] == {"pending": 3}


async def test_tiers_in_priority_order_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(crawl, "EARLIEST", int(time.time()) - 60 * 86400)
    queries = []
    c = make_crawler(tmp_path, empty_search(queries))
    await drain(c)
    order = [t.query for t in crawl.DEFAULT_TIERS]
    seen = [order.index(next(t for t in order if q.startswith(t + " "))) for q in queries]
    assert seen == sorted(seen) and set(seen) == set(range(len(order)))
    for tier in range(len(order)):
        ends = [ts(RANGE.search(q).group(2)) for q, t in zip(queries, seen) if t == tier]
        assert ends == sorted(ends, reverse=True)


async def test_resume_fills_new_gap_only(tmp_path, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(crawl, "EARLIEST", now - 365 * 86400)
    queries = []
    c = make_crawler(tmp_path, empty_search(queries), tiers=(Tier(1000, None),))
    c.storage.set_state("tier:1000+", {"upper": now - 2 * 86400, "lower": crawl.EARLIEST})
    await drain(c)
    for q in queries:
        lo, hi = (ts(x) for x in RANGE.search(q).groups())
        assert lo >= now - 2 * 86400 and hi <= now
    state = c.storage.get_state("tier:1000+")
    assert state["upper"] >= now - crawl.INDEX_LAG - 5 and "gap_top" not in state


async def test_dense_window_is_split_and_fully_paged(tmp_path, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(crawl, "EARLIEST", now - 3 * 86400)
    ids = itertools.count(1)

    def handler(request):
        lo, hi = (ts(x) for x in RANGE.search(request.url.params["q"]).groups())
        if hi - lo > 3 * 3600:
            return httpx.Response(200, json={"total_count": 1500, "incomplete_results": False, "items": []})
        page = int(request.url.params["page"])
        n = 100 if page == 1 else 50
        return httpx.Response(200, json={"total_count": 150, "incomplete_results": False,
                                         "items": [repo(next(ids)) for _ in range(n)]})

    c = make_crawler(tmp_path, handler, tiers=(Tier(0, 0),))
    await drain(c)
    stats = c.storage.stats()
    assert stats["repos"] == stats["distinct_ids"] == next(ids) - 1 > 0
    assert c.storage.get_state("tier:0-0")["lower"] == crawl.EARLIEST


async def test_search_pages_run_concurrently(tmp_path, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(crawl, "EARLIEST", now - 86400)
    ids = itertools.count(1)
    inflight, peak = 0, 0

    async def handler(request):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        return httpx.Response(200, json={"total_count": 500, "incomplete_results": False,
                                         "items": [repo(next(ids)) for _ in range(100)]})

    c = make_crawler(tmp_path, handler, tiers=(Tier(0, 0),))
    c.storage.set_state("tier:0-0", {"upper": now, "lower": now, "span": 86400 // 2})
    await c.step()
    assert peak >= 4
    assert len(c._prefetch) == 1
    await c.step()
    assert c.storage.get_state("tier:0-0")["lower"] == crawl.EARLIEST


def test_classify_graphql_results():
    tree = lambda *e: {"md": None, "root": {"entries": [{"name": n, "type": t} for n, t in e]}}
    assert crawl.classify(None) == ("rest", None)
    assert crawl.classify({"md": {"text": "hi"}, "root": None}) == ("ok", "hi")
    assert crawl.classify({"md": None, "root": None}) == ("none", None)
    assert crawl.classify(tree(("readme.txt", "blob"), ("README.rst", "blob"))) == ("fetch", "README.rst")
    assert crawl.classify(tree(("docs", "tree"), ("main.py", "blob"))) == ("rest", None)
    assert crawl.classify(tree(("main.py", "blob"))) == ("none", None)


async def test_graphql_batch_with_followup_and_split(tmp_path):
    queries = []

    def handler(request):
        q = json.loads(request.content)["query"]
        queries.append(q)
        if q.count("repository(") > 2:
            return httpx.Response(502)
        data = {}
        for alias, name in re.findall(r'(r\d+): repository\(owner: "o", name: "(\w+)"\)', q):
            if "HEAD:README.rst" in q:
                data[alias] = {"f": {"text": "rst body"}}
            elif name == "r1":
                data[alias] = {"md": {"text": "md body"}, "root": None}
            elif name == "r2":
                data[alias] = {"md": None, "root": {"entries": [{"name": "README.rst", "type": "blob"}]}}
            elif name == "r3":
                data[alias] = {"md": None, "root": None}
            else:
                data[alias] = None
        return httpx.Response(200, json={"data": data})

    c = make_crawler(tmp_path, handler, batch_size=4)
    c.storage.upsert_repos([repo(i, stars=10 - i) for i in range(1, 5)])
    rows = c.storage.claim_readmes(4)
    await c.graphql_readmes(rows)
    got = dict(c.storage.db.execute("SELECT id, readme_status FROM repos"))
    assert got == {1: "ok", 2: "ok", 3: "none", 4: "rest"}
    assert c.storage.db.execute("SELECT readme FROM repos WHERE id = 2").fetchone()[0] == "rst body"
    assert queries[0].count("repository(") == 4 and c.readmes == 2


async def test_truncated_graphql_response_splits_batch(tmp_path):
    def handler(request):
        q = json.loads(request.content)["query"]
        if q.count("repository(") > 1:
            return httpx.Response(200, content=b'{"data": {"r0": {"md": {"text": "cut')
        alias = re.search(r"(r\d+): repository", q).group(1)
        return httpx.Response(200, json={"data": {alias: {"md": {"text": "ok"}, "root": None}}})

    c = make_crawler(tmp_path, handler, batch_size=4)
    c.storage.upsert_repos([repo(i) for i in range(1, 5)])
    await c.graphql_readmes(c.storage.claim_readmes(4))
    assert c.storage.stats()["readme"] == {"ok": 4}
    dumps = list((tmp_path / "diag").glob("graphql-*.txt"))
    assert any("repos_in_batch: 4" in d.read_text(encoding="utf-8") for d in dumps)


async def test_requeue_keeps_finished_rows(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    s.upsert_repos([repo(1), repo(2)])
    s.claim_readmes(2)
    s.set_readme(1, "ok", "x")
    s.requeue([1, 2], "rest")
    assert dict(s.db.execute("SELECT id, readme_status FROM repos")) == {1: "ok", 2: "rest"}


async def test_run_stops_at_deadline_and_releases_queue(tmp_path):
    def handler(request):
        if request.url.path == "/rate_limit":
            big = {"limit": 10**9, "remaining": 10**9, "reset": int(time.time()) + 3600}
            return httpx.Response(200, json={"resources": {"core": big, "search": big, "graphql": big}})
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {}})
        if request.url.path.endswith("/readme"):
            return httpx.Response(200, text="# readme")
        return httpx.Response(200, json={"total_count": 0, "incomplete_results": False, "items": []})

    c = make_crawler(tmp_path, handler, duration=0.5, report_every=0.1)
    c.storage.upsert_repos([repo(i) for i in range(1, 40)])
    started = time.time()
    summary = await c.run()
    assert time.time() - started < 5
    assert summary["readmes"] > 0
    assert "queued" not in c.storage.stats()["readme"]
    assert {"per_min", "events", "db_readme", "cursors", "latency_s", "limiter_wait_s"} <= summary.keys()


def test_summary_log_file_keeps_only_events(tmp_path):
    import logging
    from crawler.__main__ import setup_logging

    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    try:
        path = setup_logging(str(tmp_path / "logs"))
        log = logging.getLogger("crawler")
        log.info("routine window line")
        logging.getLogger("httpx").info("HTTP Request: GET /x")
        log.warning("core rate limited")
        log.info("session summary: {}", extra=crawl.SUMMARY)
        for h in root.handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved
    assert "rate limited" in text and "session summary" in text
    assert "routine window line" not in text and "HTTP Request" not in text


async def test_next_window_is_fetched_while_current_pages_load(tmp_path, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(crawl, "EARLIEST", now - 10 * 86400)
    ids = itertools.count(1)
    windows_inflight = set()
    overlap = False

    async def handler(request):
        nonlocal overlap
        end = RANGE.search(request.url.params["q"]).group(2)
        windows_inflight.add(end)
        overlap = overlap or len(windows_inflight) > 1
        await asyncio.sleep(0.05)
        windows_inflight.discard(end)
        return httpx.Response(200, json={"total_count": 500, "incomplete_results": False,
                                         "items": [repo(next(ids)) for _ in range(100)]})

    c = make_crawler(tmp_path, handler, tiers=(Tier(0, 0),))
    c.storage.set_state("tier:0-0", {"upper": now, "lower": now, "span": 86400})
    for _ in range(3):
        await c.step()
    assert overlap
    stats = c.storage.stats()
    assert stats["repos"] == stats["distinct_ids"] == 1500


async def test_batch_ceiling_drops_after_timeout(tmp_path):
    def handler(request):
        q = json.loads(request.content)["query"]
        if q.count("repository(") > 3:
            return httpx.Response(502)
        return httpx.Response(200, json={"data": {}})

    c = make_crawler(tmp_path, handler, batch_size=8)
    c.storage.upsert_repos([repo(i) for i in range(1, 9)])
    await c.graphql_readmes(c.storage.claim_readmes(8))
    for _ in range(10):
        c.storage.upsert_repos([repo(100)])
        await c.graphql_readmes([(100, "o/r100")])
    assert c.batch_max < 8 and c.batch_size <= c.batch_max


def test_diagnostics_filename_is_windows_safe(tmp_path):
    client = GitHubClient("t", diag_dir=tmp_path)
    r = httpx.Response(200, content=b"", headers={"x-github-request-id": "D1FF:F34AF:923A9"},
                       request=httpx.Request("POST", "https://api.github.com/graphql"))
    client._dump_bad_response(r, "query { }", ValueError("x"))
    (dump,) = tmp_path.glob("graphql-*.txt")
    assert ":" not in dump.name


async def test_reset_times_are_corrected_for_clock_skew():
    from email.utils import formatdate

    server_now = time.time() - 30

    def handler(request):
        return httpx.Response(200, json={}, headers={
            "date": formatdate(server_now, usegmt=True),
            "x-ratelimit-remaining": "10", "x-ratelimit-limit": "30",
            "x-ratelimit-reset": str(int(server_now) + 60),
        })

    client = GitHubClient("t", transport=httpx.MockTransport(handler))
    await client.get("search", "/x")
    assert client.skew == pytest.approx(30, abs=2)
    assert client.limiters["search"].reset == pytest.approx(time.time() + 60, abs=3)
    m = client.metrics()
    assert m["peak_inflight"]["search"] == 1 and m["latency_s"]["search"]["max"] >= 0


def test_late_header_from_previous_window_is_ignored():
    lim = BucketLimiter("search", 30, 60)
    now = time.time()
    old_reset = now - 1
    lim.remaining, lim.reset = 2, old_reset
    lim.interval(now)
    assert lim.remaining == 30
    lim.update(remaining=1, reset=old_reset)
    assert lim.remaining == 30
    assert lim.interval(now) == pytest.approx((lim.reset - now) / 29, rel=1e-2)
