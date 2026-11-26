import json

import numpy as np

from sbs.corpus import clean, dedup, tokens
from sbs.corpus.labels import assign, classifier, threshold, topics
from tests.corpus.conftest import CLEAN_CFG, LABEL_CFG, add_repo, dedup_cfg

TOPIC_MAP = {"machine-learning": {"topics": ["pytorch", "deep-learning"]},
             "devops": {"topics": ["kubernetes", "docker"]}}
ML = "neural network training gpu tensor model layers".split()
OPS = "container cluster deploy helm pod service ingress".split()
MISC = "theme colors editor dark light palette font".split()


def test_topic_majority_and_tie():
    index = topics.invert(TOPIC_MAP)
    assert topics.label(json.dumps(["PyTorch", "deep-learning", "docker"]), index) == "machine-learning"
    assert topics.label(json.dumps(["pytorch", "docker"]), index) is None
    assert topics.label(json.dumps(["rust"]), index) is None
    assert topics.label(None, index) is None


def test_threshold_reaches_target_precision():
    classes = np.array(["a", "b", "other"])
    probs = np.array([[0.9, 0.1, 0], [0.8, 0.2, 0], [0.7, 0.3, 0], [0.6, 0.4, 0], [0.1, 0.9, 0], [0, 0.2, 0.8]])
    truth = ["a", "a", "b", "a", "b", "other"]
    t = threshold.choose(probs, classes, truth, target=0.75, min_support=1)
    assert t["a"] == {"threshold": 0.6, "cv_precision": 0.75, "cv_support": 4}
    assert threshold.choose(probs, classes, truth, target=1.0, min_support=1)["a"]["threshold"] == 0.8
    assert threshold.choose(probs, classes, truth, target=1.0, min_support=3)["a"]["threshold"] is None
    assert "other" not in t
    out = threshold.apply(np.array([[0.65, 0.35, 0], [0.55, 0.45, 0], [0.1, 0.1, 0.8]]), classes,
                          {"a": {"threshold": 0.6}, "b": {"threshold": None}})
    assert [d for d, _ in out] == ["a", classifier.UNKNOWN, classifier.OTHER]


def test_usable_drops_rare_classes():
    assert classifier.usable(["a", "a", "b"], {"cv_folds": 2}) == {"a"}


def doc(words, i):
    return " ".join(words * 4) + f" unique{i} extra{i} more{i}"


def test_source_priority_and_other(store):
    for i in range(1, 5):
        add_repo(store, i, doc(ML, i), topics=["pytorch"])
        add_repo(store, 10 + i, doc(OPS, i), topics=["docker"])
        add_repo(store, 40 + i, doc(MISC, i), topics=["vim", "theme"])
    add_repo(store, 20, doc(ML, 20), topics=["docker"], name="x/listed")
    add_repo(store, 21, doc(ML, 21), name="x/listed2")
    add_repo(store, 22, doc(OPS, 22), name="x/both")
    add_repo(store, 23, doc(ML, 23))
    add_repo(store, 24, doc(MISC, 24))
    store.upsert_awesome("a/ml", "machine-learning", "github.com/x/listed github.com/X/Listed2 github.com/x/both", 0)
    store.upsert_awesome("a/ops", "devops", "github.com/x/both", 0)
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, dedup_cfg(threshold=0.95))
    settings = {"topic_map": TOPIC_MAP, "experiment": {"corpus": {"labels": LABEL_CFG}}}
    assign.run(store, settings)
    rows = {i: (d, s) for i, d, s in store.db.execute("SELECT repo_id, domain, label_source FROM labels")}
    assert rows[1] == ("machine-learning", "topic")
    assert rows[20] == ("devops", "topic")
    assert rows[21] == ("machine-learning", "awesome")
    assert rows[22] == ("devops", "model")
    assert rows[23] == ("machine-learning", "model")
    assert rows[24] == ("other", "model")
    assert rows[41] == ("other", "model")
    stored = dict(store.db.execute("SELECT domain, threshold FROM label_thresholds"))
    assert set(stored) == {"machine-learning", "devops"}
    assert 24 not in store.labelled() and 1 in store.labelled(("topic",)) and 23 not in store.labelled(("topic",))


def test_expanded_topic_labels(store):
    for i in range(1, 4):
        add_repo(store, i, doc(ML, i), topics=["pytorch", "llm"])
        add_repo(store, 10 + i, doc(OPS, i), topics=["docker"])
    add_repo(store, 20, doc(ML, 20), topics=["llm", "rag"])
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, dedup_cfg(threshold=0.95))
    assign.run(store, {"topic_map": TOPIC_MAP, "experiment": {"corpus": {"labels": LABEL_CFG}}})
    assert store.db.execute("SELECT domain, label_source FROM labels WHERE repo_id = 20").fetchone() == (
        "machine-learning", "topic_ext")
    assert store.db.execute("SELECT topic, domain, count FROM topic_expansion").fetchall() == [
        ("llm", "machine-learning", 3)]
