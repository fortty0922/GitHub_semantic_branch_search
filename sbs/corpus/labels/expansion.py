import json
from collections import Counter, defaultdict
from collections.abc import Iterable

from sbs.corpus.labels import topics


def expand(topic_rows: Iterable[str | None], index: dict[str, set[str]], cfg: dict) -> dict[str, dict]:
    """Unmapped topics that co-occur with a single domain's mapped topics.

    Among repositories labelled by the mapped topics, an unmapped topic seen at least `min_count`
    times, with at least `min_purity` of those repositories in one domain, is added to that domain.
    """
    co: dict[str, Counter] = defaultdict(Counter)
    exclude = {t.lower() for t in cfg.get("exclude", [])}
    for topics_json in topic_rows:
        domain = topics.label(topics_json, index)
        if domain is None:
            continue
        for tag in {t.lower() for t in json.loads(topics_json or "[]")}:
            if tag not in index and tag not in exclude:
                co[tag][domain] += 1
    out = {}
    for tag, counts in co.items():
        total = sum(counts.values())
        domain, n = counts.most_common(1)[0]
        if total >= cfg["min_count"] and n / total >= cfg["min_purity"]:
            out[tag] = {"domain": domain, "count": total, "purity": n / total}
    return out


def as_index(expanded: dict[str, dict]) -> dict[str, set[str]]:
    return {tag: {e["domain"]} for tag, e in expanded.items()}
