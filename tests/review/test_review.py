from sbs.corpus import clean, dedup, split, tokens
from sbs.corpus.labels import assign
from sbs.review import checks, frames
from tests.corpus.conftest import CLEAN_CFG, LABEL_CFG, add_repo, dedup_cfg
from tests.corpus.test_labels import ML, OPS, TOPIC_MAP, doc


def build(store):
    for i in range(1, 6):
        add_repo(store, i, doc(ML, i), topics=["pytorch"], stars=i)
        add_repo(store, 10 + i, doc(OPS, i), topics=["docker"], stars=i)
    add_repo(store, 30, doc(ML, 1), stars=0, name="o/copy")
    add_repo(store, 31, "Text with a leftover | pipe and `tick`", stars=0)
    store.upsert_awesome("a/ml", "machine-learning", "github.com/o/r1 github.com/missing/repo", 0)
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, dedup_cfg())
    assign.run(store, {"topic_map": TOPIC_MAP, "experiment": {"corpus": {"labels": LABEL_CFG}}})


def test_frames(store):
    build(store)
    counts = frames.stage_counts(store)["count"]
    assert counts["repos"] == 12 and counts["dedup kept"] == 11
    assert len(frames.repos(store)) == 12 and "clean" not in frames.repos(store, [1], text=False)
    groups = frames.duplicate_groups(store, "exact")
    assert list(groups["repo_id"]) == [1, 30] and list(groups["status"]) == ["kept", "exact"]
    assert frames.duplicate_groups(store, "near").empty
    table = frames.label_crosstab(store)
    assert table.loc["machine-learning", "topic"] == 5
    assert frames.domain_agreement(store).empty
    cov = frames.awesome_coverage(store)
    assert cov.iloc[0][["links", "in_corpus"]].tolist() == [2, 1]


def test_checks(store, tmp_path):
    build(store)
    residue, snippets = checks.residue(store)
    assert residue.loc["table_pipe", "docs"] == 0 and snippets.empty
    store.db.execute("UPDATE docs_clean SET text = 'left | over https://x' WHERE repo_id = 31")
    store.db.commit()
    residue, snippets = checks.residue(store, context=5)
    assert residue.loc["url", "docs"] == 1
    assert snippets.set_index("kind").loc["table_pipe"].tolist() == [31, "left | over"]
    path = tmp_path / "test_set.json"
    assert checks.split_summary(store, path) == {"frozen": False}
    split.freeze(path, [1, 11], seed=0)
    s = checks.split_summary(store, path)
    assert s["hash_ok"] and s["disjoint"] and s["test"] == 2
    assert s["per_domain"].loc["devops", "test"] == 1
