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


def domain_report(storage: Storage, tiers: tuple[Tier, ...], target: int) -> dict:
    counts = storage.domain_counts()
    names = dict.fromkeys(t.domain for t in tiers if t.domain is not None)
    return {d: {"repos": counts.get(d, 0), "target": target, "reached": counts.get(d, 0) >= target} for d in names}


def report(storage: Storage, tiers: tuple[Tier, ...] = DEFAULT_TIERS, now: float | None = None,
           domain_target: int | None = None) -> dict:
    """A domain tier counts as finished once its domain has reached `domain_target`."""
    now = time.time() if now is None else now
    readme = readme_report(storage)
    fields = field_report(storage)
    domains = domain_report(storage, tiers, domain_target) if domain_target is not None else {}
    tier_states = {t.key: tier_report(storage, t, now) for t in tiers}
    tiers_done = all(
        (s["sweep_done"] and not s["open_gap"]) or domains.get(t.domain, {}).get("reached", False)
        for t, s in zip(tiers, tier_states.values())
    )
    complete = (
        readme["incomplete"] == 0
        and readme["failed"] == 0
        and readme["ok_without_text"] == 0
        and not any(fields.values())
        and tiers_done
    )
    total = storage.db.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    result = {"complete": complete, "repos": total, "readme": readme, "missing_fields": fields}
    if domain_target is not None:
        result["domains"] = domains
    result["tiers"] = tier_states
    return result
