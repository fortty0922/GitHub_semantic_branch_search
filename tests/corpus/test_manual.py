from sbs.corpus import clean, dedup, manual, tokens
from sbs.corpus.labels import assign
from tests.corpus.conftest import CLEAN_CFG, LABEL_CFG, add_repo, dedup_cfg
from tests.corpus.test_labels import ML, OPS, TOPIC_MAP, doc

DEDUP = dedup_cfg()


def test_round_trip(tmp_path):
    labels, excluded = tmp_path / "m" / "labels.csv", tmp_path / "m" / "exclude.csv"
    assert manual.read_labels(labels) == {} and manual.read_excluded(excluded) == {}
    manual.set_label(labels, 5, "devops", note="a, b")
    manual.set_label(labels, 2, "machine-learning")
    manual.set_label(labels, 5, "unknown")
    manual.exclude(excluded, 9, reason="link list")
    assert manual.read_labels(labels) == {2: "machine-learning", 5: "unknown"}
    assert manual.read_excluded(excluded) == {9: "link list"}
    assert manual.remove(labels, 2) and not manual.remove(labels, 2)
    assert manual.remove(excluded, 9)
    assert manual.read_labels(labels) == {5: "unknown"} and manual.read_excluded(excluded) == {}
    assert excluded.read_text(encoding="utf-8") == "repo_id,reason\n"


def test_excluded_repo_lets_duplicate_be_kept(store):
    text = " ".join(f"w{i}" for i in range(50))
    add_repo(store, 1, text, stars=100)
    add_repo(store, 2, text, stars=1)
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, DEDUP, {1: "bad", 99: "not in db"})
    assert dict(store.db.execute("SELECT repo_id, status FROM dedup")) == {1: "excluded", 2: "kept"}


def test_manual_label_wins(store):
    for i in range(1, 4):
        add_repo(store, i, doc(ML, i), topics=["pytorch"])
        add_repo(store, 10 + i, doc(OPS, i), topics=["docker"])
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, DEDUP)
    settings = {"topic_map": TOPIC_MAP, "experiment": {"corpus": {"labels": LABEL_CFG}}}
    assign.run(store, settings, {1: "devops", 11: "unknown"})
    rows = {i: (d, s) for i, d, s in store.db.execute("SELECT repo_id, domain, label_source FROM labels")}
    assert rows[1] == ("devops", "manual")
    assert rows[11] == ("unknown", "manual")
    assert rows[2] == ("machine-learning", "topic")
