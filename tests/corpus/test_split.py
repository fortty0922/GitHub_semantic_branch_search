from pathlib import Path

import pytest

from sbs.corpus import split

LABELLED = {i: ("a" if i < 50 else "b" if i < 80 else "c") for i in range(100)}
COMMITTED = Path("data/test_set.json")


def test_draw_is_stratified_and_deterministic():
    ids = split.draw(LABELLED, 0.2, seed=7)
    assert ids == split.draw(LABELLED, 0.2, seed=7)
    assert ids != split.draw(LABELLED, 0.2, seed=8)
    per_domain = {d: sum(1 for i in ids if LABELLED[i] == d) for d in "abc"}
    assert per_domain == {"a": 10, "b": 6, "c": 4}


def test_freeze_refuses_overwrite_and_detects_tampering(tmp_path):
    path = tmp_path / "data" / "test_set.json"
    split.freeze(path, [1, 3], seed=7)
    assert split.test_ids(path) == {1, 3}
    with pytest.raises(split.FrozenError):
        split.freeze(path, [2], seed=7)
    path.write_bytes(path.read_bytes().replace(b"3", b"4"))
    with pytest.raises(split.TamperedError):
        split.test_ids(path)


def test_run_train_and_test_disjoint(store, tmp_path):
    store.replace_all("dedup", ("repo_id", "status", "kept_id"),
                      [(i, "kept", None) for i in LABELLED] + [(999, "exact", 1)])
    store.replace_all("labels", ("repo_id", "domain", "label_source", "confidence"),
                      [(i, d, "topic", None) for i, d in LABELLED.items()]
                      + [(500, "unknown", "model", 0.1), (999, "a", "topic", None)])
    path = tmp_path / "test_set.json"
    settings = {"experiment": {"corpus": {"split": {"test_fraction": 0.1, "seed": 1}},
                               "paths": {"test_set": str(path)}}}
    assert split.run(store, settings) == 10
    test, train = split.test_ids(path), split.train_ids(store, path)
    assert not test & train
    assert test | train == set(LABELLED)


@pytest.mark.skipif(not COMMITTED.exists(), reason="test set not frozen yet")
def test_committed_test_set_hash_unchanged():
    assert split.test_ids(COMMITTED)
