import json
import logging
from collections import Counter

from sbs.common.store import Store
from sbs.corpus.labels import awesome, classifier, threshold, topics
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


def other_example(topics_json: str | None, index: dict, min_topics: int) -> bool:
    """Enough topics and none of them mapped to a domain: a training example for `other`."""
    tags = [t.lower() for t in json.loads(topics_json or "[]")]
    return len(tags) >= min_topics and not any(t in index for t in tags)


def run(store: Store, settings: dict, manual: dict[int, str] | None = None) -> dict[str, int]:
    manual = manual or {}
    cfg = settings["experiment"]["corpus"]["labels"]
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
            if domain is None:
                domain, source = awesome_index.get(full_name.lower()), "awesome"
        if domain is not None:
            labels[repo_id] = (domain, source, None)
            if domain != classifier.UNKNOWN:
                train_texts.append(text)
                train_labels.append(domain)
            continue
        pending.append((repo_id, text))
        if other_example(topics_json, topic_index, cfg["other_min_topics"]):
            train_texts.append(text)
            train_labels.append(classifier.OTHER)

    keep = classifier.usable(train_labels, cfg)
    dropped = sorted(set(train_labels) - keep)
    if dropped:
        log.warning("label: too few examples for %s, not trained", dropped)
    pairs = [(t, l) for t, l in zip(train_texts, train_labels) if l in keep]
    texts, truth = [t for t, _ in pairs], [l for _, l in pairs]

    thresholds: dict[str, dict] = {}
    if len(keep) >= 2:
        probs, classes = classifier.cv_probabilities(texts, truth, cfg)
        thresholds = threshold.choose(probs, classes, truth, cfg["target_precision"], cfg["min_support"])
        probs, classes = classifier.fit_predict(texts, truth, [t for _, t in pending], cfg)
        predicted = threshold.apply(probs, classes, thresholds)
    else:
        predicted = [(classifier.UNKNOWN, 0.0)] * len(pending)
    for (repo_id, _), (domain, prob) in zip(pending, predicted):
        labels[repo_id] = (domain, "model", prob)

    store.replace_all("labels", ("repo_id", "domain", "label_source", "confidence"),
                      ((i, d, s, c) for i, (d, s, c) in sorted(labels.items())))
    store.replace_all("label_thresholds", ("domain", "threshold", "cv_precision", "cv_support"),
                      ((d, t["threshold"], t["cv_precision"], t["cv_support"]) for d, t in sorted(thresholds.items())))
    log.info("label thresholds: %s", thresholds)
    counts = Counter(f"{s}:{d}" for d, s, _ in labels.values())
    log.info("label: %s", dict(sorted(counts.items())))
    return dict(counts)
