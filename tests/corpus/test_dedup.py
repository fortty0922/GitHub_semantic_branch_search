from sbs.corpus import clean, dedup, tokens
from tests.corpus.conftest import add_repo

CFG = {"min_tokens": 5, "shingle_size": 3, "num_perm": 128, "threshold": 0.8}
BASE = " ".join(f"word{i}" for i in range(60))
OTHER = " ".join(f"other{i}" for i in range(60))


def prepare(store):
    clean.run(store)
    tokens.run(store)
    return dedup.run(store, CFG)


def statuses(store):
    return {i: (s, k) for i, s, k in store.db.execute("SELECT repo_id, status, kept_id FROM dedup")}


def test_exact_and_near_duplicates_keep_most_starred(store):
    add_repo(store, 1, BASE, stars=5)
    add_repo(store, 2, "# " + BASE, stars=50)
    add_repo(store, 3, BASE + " extra", stars=1)
    add_repo(store, 4, OTHER, stars=0)
    prepare(store)
    st = statuses(store)
    assert st[2] == ("kept", None)
    assert st[1] == ("exact", 2)
    assert st[3] == ("near", 2)
    assert st[4] == ("kept", None)


def test_short_and_empty(store):
    add_repo(store, 1, "too short")
    add_repo(store, 2, None)
    add_repo(store, 3, BASE)
    prepare(store)
    st = statuses(store)
    assert (st[1][0], st[2][0], st[3][0]) == ("short", "empty", "kept")


def test_fork_filter_only_when_columns_exist(store):
    add_repo(store, 1, BASE)
    add_repo(store, 2, OTHER)
    prepare(store)
    assert statuses(store)[1][0] == "kept"
    store.db.execute("ALTER TABLE repos ADD COLUMN fork INTEGER")
    store.db.execute("ALTER TABLE repos ADD COLUMN is_template INTEGER")
    store.db.execute("UPDATE repos SET fork = 1 WHERE id = 1")
    store.db.execute("UPDATE repos SET is_template = 1 WHERE id = 2")
    store.db.commit()
    dedup.run(store, CFG)
    st = statuses(store)
    assert (st[1][0], st[2][0]) == ("fork", "template")
