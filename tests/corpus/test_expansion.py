import json

from sbs.corpus.labels import expansion, topics

INDEX = topics.invert({"ml": {"topics": ["pytorch"]}, "ops": {"topics": ["docker"]}})
CFG = {"min_count": 3, "min_purity": 0.75, "exclude": ["hacktoberfest"]}


def rows(*tag_lists):
    return [json.dumps(t) for t in tag_lists]


def test_pure_frequent_topics_are_added():
    data = rows(*[["pytorch", "llm", "hacktoberfest"]] * 4, ["docker", "llm"],
                *[["pytorch", "python"]] * 2, *[["docker", "python"]] * 2, ["llm"], ["rare", "pytorch"])
    out = expansion.expand(data, INDEX, CFG)
    assert out == {"llm": {"domain": "ml", "count": 5, "purity": 0.8}}
    assert expansion.as_index(out) == {"llm": {"ml"}}
