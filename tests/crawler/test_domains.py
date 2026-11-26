import itertools
import sqlite3
import time

import httpx

from crawler import crawl, domains
from crawler.check import report
from crawler.crawl import Tier
from crawler.storage import Storage
from tests.crawler.test_crawler import drain, make_crawler

QUERIES = {"ml": ["topic:machine-learning", "\"machine learning\" in:description"], "ops": ["topic:devops"]}


def test_tier_key_and_query_with_qualifier():
    t = Tier(10, 99, "topic:devops", "ops")
    assert t.key == "domain:ops:topic:devops:tier:10-99"
    assert t.query == "topic:devops stars:10..99"
    assert Tier(10, 99).key == "tier:10-99" and Tier(10, 99).query == "stars:10..99"


def test_domain_tiers_keep_star_priority():
    tiers = domains.domain_tiers(QUERIES, (Tier(100, None), Tier(0, 99)))
    assert [(t.lo, t.domain, t.qualifier) for t in tiers] == [
        (100, "ml", "topic:machine-learning"), (100, "ml", "\"machine learning\" in:description"),
        (100, "ops", "topic:devops"),
        (0, "ml", "topic:machine-learning"), (0, "ml", "\"machine learning\" in:description"),
        (0, "ops", "topic:devops"),
    ]


def test_load_example_config(example_config):
    queries, target = domains.load(example_config)
    assert queries and all(queries.values()) and target > 0


def test_flags_and_domains_are_stored(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    s.upsert_repos([{"id": 1, "full_name": "o/a", "fork": True, "is_template": False, "archived": False}], "ml", "q1")
    s.upsert_repos([{"id": 1, "full_name": "o/a"}], "ops", "q2")
    s.upsert_repos([{"id": 1, "full_name": "o/a"}], "ml", "q1")
    assert s.db.execute("SELECT fork, is_template, archived FROM repos").fetchone() == (1, 0, 0)
    assert s.domain_counts() == {"ml": 1, "ops": 1}
    assert s.db.execute("SELECT COUNT(*) FROM repo_domains").fetchone()[0] == 2


def test_old_database_is_migrated(tmp_path):
    path = str(tmp_path / "old.sqlite")
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE repos (id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, description TEXT, language TEXT, "
               "topics TEXT, stars INTEGER, forks INTEGER, default_branch TEXT, html_url TEXT, created_at TEXT, "
               "pushed_at TEXT, fetched_at INTEGER, readme_status TEXT NOT NULL DEFAULT 'pending', readme TEXT)")
    db.execute("INSERT INTO repos (id, full_name, readme_status, readme) VALUES (7, 'o/old', 'ok', 'x')")
    db.commit()
    db.close()
    s = Storage(path)
    assert s.db.execute("SELECT id, fork, readme FROM repos").fetchall() == [(7, None, "x")]
    s.upsert_repos([{"id": 7, "full_name": "o/old", "fork": False}])
    assert s.db.execute("SELECT fork, readme FROM repos").fetchone() == (0, "x")


async def test_domain_stops_at_target(tmp_path, monkeypatch):
    monkeypatch.setattr(crawl, "EARLIEST", int(time.time()) - 30 * 86400)
    ids = itertools.count(1)
    queries = []

    def handler(request):
        q = request.url.params["q"]
        queries.append(q)
        items = [{"id": next(ids), "full_name": f"o/r{len(queries)}-{i}"} for i in range(3)]
        return httpx.Response(200, json={"total_count": len(items), "incomplete_results": False, "items": items})

    tiers = domains.domain_tiers(QUERIES, (Tier(0, None),))
    c = make_crawler(tmp_path, handler, tiers=tiers, domain_target=5)
    await drain(c)
    counts = c.storage.domain_counts()
    assert 5 <= counts["ml"] <= 7 and 5 <= counts["ops"] <= 7
    ml_calls = [q for q in queries if "machine" in q]
    assert len(ml_calls) == 2
    assert all(q.endswith("fork:true") for q in queries)
    r = report(c.storage, tiers, domain_target=5)
    assert r["domains"]["ml"] == {"repos": counts["ml"], "target": 5, "reached": True}
    assert all(not s["sweep_done"] for s in r["tiers"].values())


def test_queries_from_topic_map_then_extra():
    doms = {"ml": {"seed_topics": ["machine-learning"], "queries": ["\"machine learning\" in:description"]},
            "os": {"seed_topics": ["operating-system"], "queries": []}}
    topic_map = {"ml": {"topics": ["pytorch", "machine-learning", "cuda"]}}
    q = domains.queries(doms, topic_map, max_topics=2)
    assert q["ml"] == ["topic:pytorch", "topic:machine-learning", "\"machine learning\" in:description"]
    assert q["os"] == ["topic:operating-system"]
