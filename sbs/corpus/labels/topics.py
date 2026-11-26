import json
from collections import Counter


def invert(topic_map: dict) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for domain, entry in topic_map.items():
        for topic in entry.get("topics", []):
            index.setdefault(topic.lower(), set()).add(domain)
    return index


def label(topics_json: str | None, index: dict[str, set[str]]) -> str | None:
    votes = Counter()
    for topic in json.loads(topics_json or "[]"):
        for domain in index.get(topic.lower(), ()):
            votes[domain] += 1
    if not votes:
        return None
    ranked = votes.most_common(2)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]
