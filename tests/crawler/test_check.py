from crawler import check
from crawler.crawl import EARLIEST, Tier
from crawler.storage import Storage

TIERS = (Tier(10, None), Tier(0, 9))
NOW = 1_800_000_000


def make(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    s.upsert_repos([{"id": i, "full_name": f"o/r{i}", "created_at": "2024-01-01T00:00:00Z"} for i in range(1, 6)])
    return s


def finish(s):
    for t in TIERS:
        s.set_state(t.key, {"upper": NOW - 3600, "lower": EARLIEST})
    for i in range(1, 6):
        s.set_readme(i, "ok", "text")


def test_empty_db_is_incomplete(tmp_path):
    r = check.report(Storage(str(tmp_path / "db.sqlite")), TIERS, NOW)
    assert not r["complete"] and r["repos"] == 0
    assert all(not t["started"] for t in r["tiers"].values())


def test_finished_db_is_complete(tmp_path):
    s = make(tmp_path)
    finish(s)
    r = check.report(s, TIERS, NOW)
    assert r["complete"]
    assert r["tiers"]["tier:10+"] == {"started": True, "sweep_done": True, "open_gap": False, "upper_age_h": 1.0}


def test_reports_each_gap(tmp_path):
    s = make(tmp_path)
    finish(s)
    s.set_readme(1, "pending", None)
    s.set_readme(2, "error", None)
    s.set_readme(3, "none", None)
    s.set_readme(4, "ok", "")
    s.db.execute("UPDATE repos SET created_at = NULL WHERE id = 5")
    s.db.commit()
    s.set_state("tier:0-9", {"upper": NOW, "lower": EARLIEST + 86400, "gap_top": NOW, "gap_cursor": NOW})
    r = check.report(s, TIERS, NOW)
    assert not r["complete"]
    assert (r["readme"]["incomplete"], r["readme"]["failed"], r["readme"]["done"]) == (1, 1, 3)
    assert r["readme"]["ok_without_text"] == 1
    assert r["missing_fields"] == {"full_name": 0, "created_at": 1, "fetched_at": 0}
    assert r["tiers"]["tier:0-9"]["open_gap"] and not r["tiers"]["tier:0-9"]["sweep_done"]
