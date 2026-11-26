import json
from collections import Counter, defaultdict
from collections.abc import Iterable


def topic_sets(rows: Iterable[str | None]) -> list[frozenset[str]]:
    """Topic sets of the background repositories. Identical multi-topic sets count once: spam
    campaigns publish hundreds of repositories with the same topic list, which would otherwise
    make their topics look strongly associated with any seed they include."""
    sets, seen = [], set()
    for topics_json in rows:
        tags = frozenset(t.lower() for t in json.loads(topics_json or "[]"))
        if not tags or (len(tags) > 1 and tags in seen):
            continue
        seen.add(tags)
        sets.append(tags)
    return sets


def candidates(repos: list[frozenset[str]], owner: dict[str, str], seed_owner: dict[str, str],
               counts: Counter, cfg: dict) -> list[tuple]:
    """(domain, topic, assoc, lift, seed_assoc, count) for unassigned topics that pass every criterion."""
    total = len(repos)
    max_count = cfg["max_df"] * total
    co: dict[str, Counter] = defaultdict(Counter)
    seed_co: dict[str, Counter] = defaultdict(Counter)
    members = Counter()
    for tags in repos:
        domains = {owner[t] for t in tags if t in owner}
        if not domains:
            continue
        members.update(domains)
        seeded = {seed_owner[t] for t in tags if t in seed_owner}
        for t in tags:
            if t not in owner and cfg["min_count"] <= counts[t] <= max_count:
                co[t].update(domains)
                seed_co[t].update(seeded)
    out = []
    for t, per_domain in co.items():
        ranked = sorted(((n / counts[t], d) for d, n in per_domain.items()), reverse=True)
        assoc, d = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        lift = assoc / (members[d] / total)
        seed_assoc = seed_co[t][d] / counts[t]
        if (assoc >= cfg["min_assoc"] and lift >= cfg["min_lift"] and assoc >= cfg["min_margin"] * runner_up
                and seed_assoc >= cfg["min_seed_assoc"]):
            out.append((d, t, assoc, lift, seed_assoc, counts[t]))
    return out


def discover(repos: list[frozenset[str]], seeds: dict[str, list[str]], cfg: dict) -> dict[str, dict]:
    """Grow each domain's topic set from its seeds by co-occurrence over a background sample.

    An unassigned topic with `min_count` to `max_df` (share of repositories) occurrences joins
    domain D when, among its repositories, the share also carrying one of D's current topics
    (assoc) is at least `min_assoc`, at least `min_lift` times D's share of all repositories, and
    at least `min_margin` times the share for any other domain; and at least `min_seed_assoc` of its
    repositories carry one of D's original seeds, which anchors every round to the seeds and stops
    chains drifting away from them. Per round each domain takes at most `max_new_per_round`
    topics, most frequent first (frequent topics matter most for search and labels, and all of
    them already passed every criterion); rounds stop after `max_rounds` or when nothing is added.
    Returns {topic: {domain, round, count, assoc, lift, seed_assoc}}.
    """
    counts = Counter(t for tags in repos for t in tags)
    seed_owner = {t.lower(): d for d, ts in seeds.items() for t in ts}
    found = {t: {"domain": d, "round": 0, "count": counts.get(t, 0), "assoc": 1.0, "lift": None, "seed_assoc": 1.0}
             for t, d in seed_owner.items()}
    for rnd in range(1, cfg["max_rounds"] + 1):
        owner = {t: e["domain"] for t, e in found.items()}
        by_domain: dict[str, list[tuple]] = defaultdict(list)
        for d, t, assoc, lift, seed_assoc, count in candidates(repos, owner, seed_owner, counts, cfg):
            by_domain[d].append((count, assoc, t, lift, seed_assoc))
        added = 0
        for d, items in by_domain.items():
            for count, assoc, t, lift, seed_assoc in sorted(items, reverse=True)[: cfg["max_new_per_round"]]:
                found[t] = {"domain": d, "round": rnd, "count": count, "assoc": assoc, "lift": lift,
                            "seed_assoc": seed_assoc}
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
