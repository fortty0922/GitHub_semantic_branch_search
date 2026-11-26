from sbs.corpus import clean, dedup, tokens
from tests.corpus.conftest import CLEAN_CFG, add_repo, dedup_cfg

CFG = dedup_cfg(min_tokens=5, threshold=0.8)
BASE = " ".join(f"word{i}" for i in range(60))
OTHER = " ".join(f"other{i}" for i in range(60))


def prepare(store):
    clean.run(store, CLEAN_CFG)
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
    for column in ("fork", "is_template", "archived"):
        store.db.execute(f"ALTER TABLE repos DROP COLUMN {column}")
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


def test_non_english_docs_are_dropped(store):
    add_repo(store, 1, "這是一個用於訓練神經網路的工具，支援多種模型與資料集，" * 5 + " pytorch")
    add_repo(store, 2, BASE)
    prepare(store)
    st = statuses(store)
    assert st[1][0] == "non_english" and st[2][0] == "kept"


def test_large_groups_are_boilerplate_unless_dominated(store):
    for i in range(1, 5):
        add_repo(store, i, BASE, stars=100 + i)
    add_repo(store, 10, OTHER, stars=5000)
    for i in range(11, 14):
        add_repo(store, i, OTHER, stars=1)
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, dedup_cfg(max_group_size=4))
    st = statuses(store)
    assert all(st[i] == ("boilerplate", None) for i in range(1, 5))
    assert st[10] == ("kept", None) and st[11] == ("exact", 10)


def test_mirror_group_keeps_original(store):
    add_repo(store, 1, BASE, stars=100, name="torvalds/linux")
    for i, name in enumerate(["a/linux", "b/linux-fork", "c/Linux", "d/kernel"], start=2):
        add_repo(store, i, BASE, stars=50, name=name)
    clean.run(store, CLEAN_CFG)
    tokens.run(store)
    dedup.run(store, dedup_cfg(max_group_size=4))
    st = statuses(store)
    assert st[1] == ("kept", None) and st[5] == ("exact", 1)
