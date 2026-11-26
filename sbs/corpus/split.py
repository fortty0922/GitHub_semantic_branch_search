import hashlib
import json
import logging
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

from sbs.common.store import Store

log = logging.getLogger("sbs")


class FrozenError(Exception):
    pass


class TamperedError(Exception):
    pass


def hash_path(path: Path) -> Path:
    return path.with_suffix(".sha256")


def draw(labelled: dict[int, str], fraction: float, seed: int) -> list[int]:
    by_domain = defaultdict(list)
    for repo_id, domain in labelled.items():
        by_domain[domain].append(repo_id)
    rng = random.Random(seed)
    test = []
    for domain in sorted(by_domain):
        ids = sorted(by_domain[domain])
        rng.shuffle(ids)
        test.extend(ids[:round(len(ids) * fraction)])
    return sorted(test)


def freeze(path: Path, ids: list[int], seed: int) -> None:
    if path.exists() or hash_path(path).exists():
        raise FrozenError(f"{path} already exists; the test set is frozen")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"created": date.today().isoformat(), "seed": seed, "ids": ids}, indent=0).encode()
    path.write_bytes(body)
    hash_path(path).write_text(hashlib.sha256(body).hexdigest() + "\n", encoding="ascii")


def test_ids(path: str | Path) -> set[int]:
    path = Path(path)
    body = path.read_bytes()
    expected = hash_path(path).read_text(encoding="ascii").strip()
    if hashlib.sha256(body).hexdigest() != expected:
        raise TamperedError(f"{path} does not match {hash_path(path)}")
    return set(json.loads(body)["ids"])


def train_ids(store: Store, path: str | Path) -> set[int]:
    return set(store.labelled()) - test_ids(path)


def run(store: Store, settings: dict) -> int:
    cfg = settings["experiment"]["corpus"]["split"]
    path = Path(settings["experiment"]["paths"]["test_set"])
    ids = draw(store.labelled(), cfg["test_fraction"], cfg["seed"])
    freeze(path, ids, cfg["seed"])
    log.info("split: %d test ids frozen in %s", len(ids), path)
    return len(ids)
