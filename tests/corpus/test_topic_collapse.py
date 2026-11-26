import random

from sbs.corpus.labels import topic_collapse

CFG = {"collapse_min_topics": 5, "collapse_threshold": 0.7, "collapse_num_perm": 128}
CORE = [f"spam{i}" for i in range(18)]


def test_near_identical_long_lists_collapse():
    rng = random.Random(0)
    spam = [frozenset(rng.sample(CORE, 16) + [f"x{i}", f"y{i}"]) for i in range(200)]
    legit = [frozenset({"vulkan", "renderer", "pbr", "c++", "engine", f"own{i}"}) for i in range(3)]
    other = [frozenset({"django", "rest-api", "jwt", "postgres", "docker", "celery"})]
    short = [frozenset({"vulkan", "opengl"})] * 5
    kept, dropped = topic_collapse.collapse_near(spam + legit + other + short, CFG)
    kept_spam = [s for s in kept if s & set(CORE)]
    assert len(kept_spam) < 30 and dropped >= 200 - len(kept_spam)
    assert other[0] in kept and kept.count(short[0]) == 5


def test_nothing_to_collapse():
    sets = [frozenset({"a", "b"}), frozenset({"c"})]
    assert topic_collapse.collapse_near(sets, CFG) == (sets, 0)
