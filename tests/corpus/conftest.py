import json

import pytest

from crawler.storage import Storage
from sbs.common.store import Store


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db.sqlite")
    Storage(path).close()
    return path


@pytest.fixture
def store(db_path):
    s = Store(db_path)
    yield s
    s.close()


def add_repo(store, repo_id, readme=None, stars=0, topics=(), description=None, name=None):
    store.db.execute(
        "INSERT INTO repos (id, full_name, description, topics, stars, readme_status, readme) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (repo_id, name or f"o/r{repo_id}", description, json.dumps(list(topics)), stars,
         "ok" if readme is not None else "none", readme),
    )
    store.db.commit()


def dedup_cfg(**overrides):
    cfg = {"min_tokens": 1, "shingle_size": 3, "num_perm": 128, "threshold": 0.9,
           "min_english_ratio": 0.5, "max_group_size": 1000, "keep_star_ratio": 10.0, "mirror_share": 0.5}
    return cfg | overrides


LABEL_CFG = {"target_precision": 0.5, "min_support": 1, "cv_folds": 2, "seed": 0, "other_min_topics": 2,
             "max_features": 1000, "min_df": 1, "C": 10.0,
             "expansion": {"min_count": 2, "min_purity": 0.9, "exclude": []}}
CLEAN_CFG = {"inline_code_max_words": 3}
