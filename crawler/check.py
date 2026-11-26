import time

from .crawl import DEFAULT_TIERS, EARLIEST, Tier
from .storage import Storage

DONE = ("ok", "none")
INCOMPLETE = ("pending", "rest", "queued")
FAILED = ("error",)
REQUIRED_FIELDS = ("full_name", "created_at", "fetched_at")


def readme_report(storage: Storage) -> dict:
    counts = dict(storage.db.execute("SELECT readme_status, COUNT(*) FROM repos GROUP BY readme_status"))
    ok_empty = storage.db.execute(
        "SELECT COUNT(*) FROM repos WHERE readme_status = 'ok' AND (readme IS NULL OR readme = '')"
    ).fetchone()[0]
    return {
        "by_status": counts,
        "done": sum(counts.get(s, 0) for s in DONE),
        "incomplete": sum(counts.get(s, 0) for s in INCOMPLETE),
        "failed": sum(counts.get(s, 0) for s in FAILED),
        "ok_without_text": ok_empty,
    }


def field_report(storage: Storage) -> dict:
    return {
        f: storage.db.execute(f"SELECT COUNT(*) FROM repos WHERE {f} IS NULL OR {f} = ''").fetchone()[0]
        for f in REQUIRED_FIELDS
    }


def tier_report(storage: Storage, tier: Tier, now: float) -> dict:
    state = storage.get_state(tier.key)
    if not state:
        return {"started": False, "sweep_done": False, "open_gap": False, "upper_age_h": None}
    return {
        "started": True,
        "sweep_done": state["lower"] <= EARLIEST,
        "open_gap": "gap_top" in state,
        "upper_age_h": round((now - state["upper"]) / 3600, 1),
    }


def report(storage: Storage, tiers: tuple[Tier, ...] = DEFAULT_TIERS, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    readme = readme_report(storage)
    fields = field_report(storage)
    tier_states = {t.key: tier_report(storage, t, now) for t in tiers}
    complete = (
        readme["incomplete"] == 0
        and readme["failed"] == 0
        and readme["ok_without_text"] == 0
        and not any(fields.values())
        and all(s["sweep_done"] and not s["open_gap"] for s in tier_states.values())
    )
    total = storage.db.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    return {"complete": complete, "repos": total, "readme": readme, "missing_fields": fields, "tiers": tier_states}
