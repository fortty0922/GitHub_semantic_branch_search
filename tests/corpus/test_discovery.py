import tomllib

from sbs.common import settings as settings_mod
from sbs.corpus.labels import discover_topics, discovery, topic_map_file
from tests.corpus.conftest import add_repo

SEEDS = {"ml": ["machine-learning"], "ops": ["devops"]}
CFG = {"min_count": 3, "min_assoc": 0.5, "max_rounds": 3}


def sets(*rows):
    return [frozenset(r) for r in rows]


def test_discovery_grows_from_seeds_and_skips_ambiguous_and_rare():
    repos = sets(
        *[["machine-learning", "pytorch", "python"]] * 3,
        *[["pytorch", "torchvision"]] * 3,
        *[["devops", "kubernetes", "python"]] * 3,
        *[["kubernetes", "helm"]] * 2,
        *[["python", "web"]] * 4,
        ["machine-learning", "rare"],
    )
    found = discovery.discover(repos, SEEDS, CFG)
    assert found["pytorch"] == {"domain": "ml", "round": 1, "count": 6, "assoc": 0.5}
    assert found["torchvision"]["domain"] == "ml" and found["torchvision"]["round"] == 2
    assert found["kubernetes"]["domain"] == "ops"
    assert "python" not in found and "rare" not in found and "helm" not in found
    assert discovery.by_domain(found, ["ml", "ops"])["ml"] == ["pytorch", "machine-learning", "torchvision"]


def test_discovery_stops_at_max_rounds():
    repos = sets(*[["machine-learning", "a"]] * 3, *[["a", "b"]] * 3, *[["b", "c"]] * 3)
    found = discovery.discover(repos, SEEDS, dict(CFG, max_rounds=1))
    assert set(found) == {"machine-learning", "devops", "a"}


def test_written_topic_map_round_trips(tmp_path):
    path = tmp_path / "topic_map.toml"
    topic_map_file.write(path, {"ml": ["pytorch", 'odd"name'], "ops": []})
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "ml": {"topics": ["pytorch", 'odd"name']}, "ops": {"topics": []}}


def test_run_writes_topic_map_into_config_dir(store, example_config):
    config = example_config
    (config / "domains.toml").write_text(
        '[ml]\nseed_topics = ["machine-learning"]\nqueries = []\n\n[ops]\nseed_topics = ["devops"]\nqueries = []\n',
        encoding="utf-8")
    settings = settings_mod.load(config)
    settings["experiment"]["corpus"]["topics"] = CFG
    for i in range(1, 7):
        add_repo(store, i, None, topics=["machine-learning", "pytorch"] if i <= 3 else ["pytorch", "cuda"])
    discover_topics.run(store, settings)
    written = tomllib.loads((config / "topic_map.toml").read_text(encoding="utf-8"))
    assert written == {"ml": {"topics": ["pytorch", "cuda", "machine-learning"]}, "ops": {"topics": ["devops"]}}
    assert store.db.execute("SELECT COUNT(*) FROM topic_discovery").fetchone()[0] == 4
