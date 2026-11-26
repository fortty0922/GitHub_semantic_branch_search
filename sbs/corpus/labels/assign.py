import json
import logging
from collections import Counter

from sbs.common.store import Store
from sbs.corpus.labels import awesome, classifier, topics
from sbs.corpus.tokens import tokenize

log = logging.getLogger("sbs")

KEPT = """
SELECT r.id, r.full_name, r.description, r.topics, t.text
FROM dedup d JOIN repos r ON r.id = d.repo_id JOIN tokens t ON t.repo_id = d.repo_id
WHERE d.status = 'kept' ORDER BY r.id
"""


def features(description: str | None, topics_json: str | None, token_text: str) -> str:
    head = " ".join(w for s in tokenize(description or "") for w in s)
    tags = " ".join(json.loads(topics_json or "[]"))
    return " ".join((head, tags, token_text.replace("\n", " ")))


def run(store: Store, settings: dict, manual: dict[int, str] | None = None) -> dict[str, int]:
    manual = manual or {}
    topic_index = topics.invert(settings["topic_map"])
    awesome_index = awesome.index(store)
    labels: dict[int, tuple[str, str, float | None]] = {}
    pending: list[tuple[int, str]] = []
    train_texts, train_labels = [], []
    for repo_id, full_name, description, topics_json, token_text in store.iter_rows(KEPT):
        text = features(description, topics_json, token_text)
        if repo_id in manual:
            domain, source = manual[repo_id], "manual"
        else:
            domain, source = topics.label(topics_json, topic_index), "topic"
        if domain == classifier.UNKNOWN:
            labels[repo_id] = (domain, source, None)
            continue
        if domain is None:
            domain, source = awesome_index.get(full_name.lower()), "awesome"
        if domain is None:
            pending.append((repo_id, text))
            continue
        labels[repo_id] = (domain, source, None)
        train_texts.append(text)
        train_labels.append(domain)

    predicted = classifier.fit_predict(train_texts, train_labels, [t for _, t in pending],
                                       settings["experiment"]["corpus"]["labels"])
    for (repo_id, _), (domain, prob) in zip(pending, predicted):
        labels[repo_id] = (domain, "model", prob)

    store.replace_all("labels", ("repo_id", "domain", "label_source", "confidence"),
                      ((i, d, s, c) for i, (d, s, c) in sorted(labels.items())))
    counts = Counter(f"{s}:{d}" for d, s, _ in labels.values())
    log.info("label: %s", dict(sorted(counts.items())))
    return dict(counts)
