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
