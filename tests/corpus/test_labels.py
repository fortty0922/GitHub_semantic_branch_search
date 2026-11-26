import json

from sbs.corpus import clean, dedup, tokens
from sbs.corpus.labels import assign, classifier, topics
from tests.corpus.conftest import add_repo

TOPIC_MAP = {"machine-learning": {"topics": ["pytorch", "deep-learning"]},
             "devops": {"topics": ["kubernetes", "docker"]}}
LABEL_CFG = {"min_confidence": 0.5, "max_features": 1000, "min_df": 1, "C": 10.0}
ML = "neural network training gpu tensor model layers".split()
OPS = "container cluster deploy helm pod service ingress".split()


def test_topic_majority_and_tie():
    index = topics.invert(TOPIC_MAP)
    assert topics.label(json.dumps(["PyTorch", "deep-learning", "docker"]), index) == "machine-learning"
    assert topics.label(json.dumps(["pytorch", "docker"]), index) is None
    assert topics.label(json.dumps(["rust"]), index) is None
    assert topics.label(None, index) is None


def test_classifier_unknown_below_confidence():
    train = ["neural network training gpu"] * 3 + ["container cluster deploy helm"] * 3
    labels = ["machine-learning"] * 3 + ["devops"] * 3
    assert classifier.fit_predict(train, labels, ["neural network gpu"], LABEL_CFG)[0][0] == "machine-learning"
    strict = dict(LABEL_CFG, min_confidence=0.99)
    assert classifier.fit_predict(train, labels, ["unrelated words"], strict)[0][0] == classifier.UNKNOWN


def test_classifier_needs_two_classes():
    assert classifier.fit_predict(["a"], ["devops"], ["b"], LABEL_CFG) == [(classifier.UNKNOWN, 0.0)]


def doc(words, i):
    return " ".join(words * 4) + f" unique{i} extra{i} more{i}"


def test_source_priority(store):
    for i in range(1, 4):
        add_repo(store, i, doc(ML, i), topics=["pytorch"])
        add_repo(store, 10 + i, doc(OPS, i), topics=["docker"])
    add_repo(store, 20, doc(ML, 20), topics=["docker"], name="x/listed")
    add_repo(store, 21, doc(ML, 21), name="x/listed2")
    add_repo(store, 22, doc(OPS, 22), name="x/both")
    add_repo(store, 23, doc(ML, 23))
    store.upsert_awesome("a/ml", "machine-learning", "github.com/x/listed github.com/X/Listed2 github.com/x/both", 0)
    store.upsert_awesome("a/ops", "devops", "github.com/x/both", 0)
    clean.run(store)
    tokens.run(store)
    dedup.run(store, {"min_tokens": 1, "shingle_size": 3, "num_perm": 128, "threshold": 0.95})
    settings = {"topic_map": TOPIC_MAP, "experiment": {"corpus": {"labels": LABEL_CFG}}}
    assign.run(store, settings)
    rows = {i: (d, s) for i, d, s in store.db.execute("SELECT repo_id, domain, label_source FROM labels")}
    assert rows[1] == ("machine-learning", "topic")
    assert rows[20] == ("devops", "topic")
    assert rows[21] == ("machine-learning", "awesome")
    assert rows[22] == ("devops", "model")
    assert rows[23] == ("machine-learning", "model")
