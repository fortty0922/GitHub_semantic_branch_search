from sbs.common import settings


def test_loads_config_files():
    s = settings.load("config")
    assert set(s) == set(settings.FILES)
    assert set(s["topic_map"]) == set(s["awesome_lists"])
    for key in ("dedup", "labels", "split"):
        assert key in s["experiment"]["corpus"]
