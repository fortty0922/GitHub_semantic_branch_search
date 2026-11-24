import asyncio
import itertools
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
    client = GitHubClient("t", transport=httpx.MockTransport(handler))
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


async def test_run_stops_at_deadline_and_releases_queue(tmp_path):
    def handler(request):
        if request.url.path == "/rate_limit":
            big = {"limit": 10**9, "remaining": 10**9, "reset": int(time.time()) + 3600}
            return httpx.Response(200, json={"resources": {"core": big, "search": big}})
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
