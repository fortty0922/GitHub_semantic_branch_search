import json
from collections import Counter, defaultdict
from collections.abc import Iterable


def topic_sets(rows: Iterable[str | None]) -> list[frozenset[str]]:
    sets = []
    for topics_json in rows:
        tags = frozenset(t.lower() for t in json.loads(topics_json or "[]"))
        if tags:
            sets.append(tags)
    return sets


def discover(repos: list[frozenset[str]], seeds: dict[str, list[str]], cfg: dict) -> dict[str, dict]:
    """Grow each domain's topic set from its seeds by co-occurrence over a background sample.

    In each round, an unassigned topic seen in at least `min_count` repositories joins a domain
    when at least `min_assoc` of those repositories also carry one of the domain's current topics,
    and no other domain reaches `min_assoc` (ambiguous topics stay out). Stops after `max_rounds`
    rounds or when a round adds nothing. Returns {topic: {domain, round, count, assoc}}.
    """
    counts = Counter(t for tags in repos for t in tags)
    found = {t.lower(): {"domain": d, "round": 0, "count": counts.get(t.lower(), 0), "assoc": 1.0}
             for d, ts in seeds.items() for t in ts}
    for rnd in range(1, cfg["max_rounds"] + 1):
        owner = {t: e["domain"] for t, e in found.items()}
        co: dict[str, Counter] = defaultdict(Counter)
        for tags in repos:
            domains = {owner[t] for t in tags if t in owner}
            if not domains:
                continue
            for t in tags:
                if t not in owner and counts[t] >= cfg["min_count"]:
                    for d in domains:
                        co[t][d] += 1
        added = 0
        for t, per_domain in co.items():
            strong = [(n / counts[t], d) for d, n in per_domain.items() if n / counts[t] >= cfg["min_assoc"]]
            if len(strong) == 1:
                assoc, d = strong[0]
                found[t] = {"domain": d, "round": rnd, "count": counts[t], "assoc": assoc}
                added += 1
        if not added:
            break
    return found


def by_domain(found: dict[str, dict], domains: list[str]) -> dict[str, list[str]]:
    """Topics per domain, most frequent first (seeds included)."""
    out = {d: [] for d in domains}
    for t, e in sorted(found.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        out[e["domain"]].append(t)
    return out
