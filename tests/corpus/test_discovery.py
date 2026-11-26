import tomllib

from sbs.common import settings as settings_mod
from sbs.corpus.labels import discover_topics, discovery, topic_map_file
from tests.corpus.conftest import add_repo

SEEDS = {"ml": ["machine-learning"], "ops": ["devops"]}
CFG = {"min_count": 3, "max_df": 0.3, "min_assoc": 0.5, "min_lift": 2.0, "min_margin": 2.0,
       "max_new_per_round": 10, "max_rounds": 3, "min_seed_assoc": 0.0,
       "collapse_min_topics": 5, "collapse_threshold": 0.7, "collapse_num_perm": 64, "max_query_round": 1}


def sets(*rows):
    return [frozenset(r) for r in rows]


BACKGROUND = sets(
    *[["machine-learning", "pytorch", "python"]] * 4,
    *[["pytorch", "torchvision"]] * 4,
    *[["devops", "kubernetes", "python"]] * 4,
    *[["kubernetes", "helm"]] * 3,
    *[["python", "web"]] * 20,
    *[["machine-learning", "rare"]] * 2,
    *[["misc"]] * 60,
)


def test_discovery_grows_from_seeds_in_rounds():
    found = discovery.discover(BACKGROUND, SEEDS, CFG)
    assert found["pytorch"]["domain"] == "ml" and found["pytorch"]["round"] == 1
    assert found["pytorch"]["assoc"] == 0.5 and found["pytorch"]["lift"] > 8
    assert found["kubernetes"]["domain"] == "ops" and found["kubernetes"]["round"] == 1
    assert (found["torchvision"]["domain"], found["torchvision"]["round"]) == ("ml", 2)
    assert (found["helm"]["domain"], found["helm"]["round"]) == ("ops", 2)
    assert {"python", "rare", "misc", "web"}.isdisjoint(found)
    assert discovery.by_domain(found, ["ml", "ops"])["ml"] == ["pytorch", "machine-learning", "torchvision"]
    assert discovery.by_domain(found, ["ml", "ops"], max_round=1)["ml"] == ["pytorch", "machine-learning"]


def test_seed_anchor_stops_drifting_chains():
    anchored = discovery.discover(BACKGROUND, SEEDS, dict(CFG, min_seed_assoc=0.1))
    assert "pytorch" in anchored and "kubernetes" in anchored
    assert "torchvision" not in anchored and "helm" not in anchored
    assert anchored["pytorch"]["seed_assoc"] == 0.5


def test_frequent_topics_are_never_candidates():
    loose = dict(CFG, min_assoc=0.1, min_lift=1.0, min_margin=1.0)
    assert "python" in discovery.discover(BACKGROUND, SEEDS, loose)
    assert "python" not in discovery.discover(BACKGROUND, SEEDS, dict(loose, max_df=0.2))


def test_shared_topics_fail_the_margin():
    repos = BACKGROUND + sets(*[["machine-learning", "devops", "shared"]] * 3)
    assert "shared" not in discovery.discover(repos, SEEDS, CFG)
    assert discovery.discover(repos, SEEDS, dict(CFG, min_margin=1.0)).get("shared") is not None


def test_round_cap_and_max_rounds():
    repos = BACKGROUND + sets(*[["machine-learning", "jax"]] * 6)
    capped = discovery.discover(repos, SEEDS, dict(CFG, max_new_per_round=1, max_rounds=1))
    assert [t for t, e in capped.items() if e["round"] == 1 and e["domain"] == "ml"] == ["pytorch"]
    assert capped["pytorch"]["count"] > discovery.discover(repos, SEEDS, CFG)["jax"]["count"]
    assert "torchvision" not in discovery.discover(BACKGROUND, SEEDS, dict(CFG, max_rounds=1))


def test_written_topic_map_round_trips(tmp_path):
    path = tmp_path / "topic_map.toml"
    topic_map_file.write(path, {"ml": ["pytorch", 'odd"name'], "ops": []})
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "ml": {"topics": ["pytorch", 'odd"name']}, "ops": {"topics": []}}
    topic_map_file.write(path, {"ml": ["pytorch", "cuda"]}, {"ml": ["pytorch"]})
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {"ml": {"topics": ["pytorch", "cuda"], "query_topics": ["pytorch"]}}


def test_run_writes_topic_map_into_config_dir(store, example_config):
    config = example_config
    (config / "domains.toml").write_text(
        '[ml]\nseed_topics = ["machine-learning"]\nqueries = []\n\n[ops]\nseed_topics = ["devops"]\nqueries = []\n',
        encoding="utf-8")
    settings = settings_mod.load(config)
    settings["experiment"]["corpus"]["topics"] = CFG
    for i in range(1, 7):
        tags = ["machine-learning", "pytorch"] if i <= 3 else ["pytorch", "cuda"]
        add_repo(store, i, None, topics=[*tags, f"u{i}"])
    for i in range(10, 40):
        add_repo(store, i, None, topics=["misc", f"u{i}"])
    for i in range(50, 60):
        add_repo(store, i, None, topics=["machine-learning", "spam", f"u{i}"])
    for i in range(60, 70):
        add_repo(store, i, None, topics=["machine-learning", "clone"])
    discover_topics.run(store, settings)
    before = tomllib.loads((config / "topic_map.toml").read_text(encoding="utf-8"))["ml"]["topics"]
    assert "spam" in before and "clone" not in before
    store.replace_all("dedup", ("repo_id", "status", "kept_id"),
                      [(i, "kept", None) for i in [*range(1, 7), *range(10, 40), *range(60, 70)]]
                      + [(i, "boilerplate", None) for i in range(50, 60)])
    discover_topics.run(store, settings)
    written = tomllib.loads((config / "topic_map.toml").read_text(encoding="utf-8"))
    assert written == {
        "ml": {"topics": ["pytorch", "machine-learning", "cuda"], "query_topics": ["pytorch", "machine-learning"]},
        "ops": {"topics": ["devops"], "query_topics": ["devops"]},
    }
    assert store.db.execute("SELECT COUNT(*) FROM topic_discovery").fetchone()[0] == 4
    store.reset("topic_discovery")
    assert store.has_column("topic_discovery", "lift")


def test_identical_topic_lists_count_once():
    rows = ['["a", "b"]', '["b", "a"]', '["A", "b"]', '["solo"]', '["solo"]', "[]", None]
    assert discovery.topic_sets(rows) == [frozenset({"a", "b"}), frozenset({"solo"}), frozenset({"solo"})]
