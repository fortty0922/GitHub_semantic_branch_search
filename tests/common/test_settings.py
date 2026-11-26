import pytest

from sbs.common import settings


def test_loads_example_config(example_config):
    s = settings.load(example_config)
    assert set(s) == set(settings.FILES) | {"config_dir"}
    assert set(s["domains"]) == set(s["topic_map"]) == set(s["awesome_lists"])
    assert all(e["seed_topics"] for e in s["domains"].values())
    for key in ("topics", "clean", "dedup", "labels", "split"):
        assert key in s["experiment"]["corpus"]


def test_missing_config_points_to_example(example_config):
    (example_config / "domains.toml").unlink()
    with pytest.raises(settings.MissingConfigError, match="domains.toml.example"):
        settings.load(example_config)
