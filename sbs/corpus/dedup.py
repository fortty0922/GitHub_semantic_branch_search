import hashlib
import logging
from collections import defaultdict

from datasketch import LeanMinHash, MinHash, MinHashLSH

from sbs.common.store import Store
from sbs.corpus.language import english_ratio
from sbs.corpus.tokens import deserialize

log = logging.getLogger("sbs")

FLAG_COLUMNS = (("fork", "fork"), ("is_template", "template"))


def flagged(store: Store) -> dict[int, str]:
    out = {}
    for column, status in FLAG_COLUMNS:
        if not store.has_column("repos", column):
            log.info("dedup: repos.%s missing, %s filter skipped", column, status)
            continue
        for (repo_id,) in store.db.execute(f"SELECT id FROM repos WHERE {column} = 1"):
            out.setdefault(repo_id, status)
    return out


def shingles(words: list[str], size: int) -> set[bytes]:
    if len(words) <= size:
        return {" ".join(words).encode()}
    return {" ".join(words[i:i + size]).encode() for i in range(len(words) - size + 1)}


class UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def best(ids: list[int], stars: dict[int, int]) -> int:
    return min(ids, key=lambda i: (-stars.get(i, 0), i))


def resolve(groups: list[list[int]], stars: dict[int, int], status: str, result: dict) -> None:
    for ids in groups:
        if len(ids) < 2:
            continue
        keep = best(ids, stars)
        for i in ids:
            if i != keep:
                result[i] = (status, keep)


def near_groups(docs: dict[int, list[str]], cfg: dict) -> list[list[int]]:
    ids = sorted(docs)
    sets = [shingles(docs[i], cfg["shingle_size"]) for i in ids]
    lsh = MinHashLSH(threshold=cfg["threshold"], num_perm=cfg["num_perm"])
    hashes = {}
    for repo_id, mh in zip(ids, MinHash.bulk(sets, num_perm=cfg["num_perm"])):
        lean = LeanMinHash(mh)
        hashes[repo_id] = lean
        lsh.insert(repo_id, lean)
    uf = UnionFind()
    for repo_id in ids:
        for other in lsh.query(hashes[repo_id]):
            if other != repo_id:
                uf.union(repo_id, other)
    groups = defaultdict(list)
    for repo_id in uf.parent:
        groups[uf.find(repo_id)].append(repo_id)
    return list(groups.values())


def non_english(store: Store, skip: dict, min_ratio: float) -> dict[int, tuple[str, None]]:
    return {
        repo_id: ("non_english", None)
        for repo_id, text in store.iter_rows("SELECT repo_id, text FROM docs_clean")
        if repo_id not in skip and english_ratio(text) < min_ratio
    }


def final_kept(repo_id: int, result: dict) -> int:
    while result[repo_id][0] in ("exact", "near"):
        repo_id = result[repo_id][1]
    return repo_id


def boilerplate(result: dict, stars: dict[int, int], cfg: dict) -> None:
    """Large duplicate groups are template READMEs or spam: drop the whole group, unless the kept
    repository clearly dominates by stars (an original with its copies)."""
    groups = defaultdict(list)
    for repo_id, (status, _) in result.items():
        if status in ("exact", "near"):
            groups[final_kept(repo_id, result)].append(repo_id)
    for keep, members in groups.items():
        if len(members) + 1 < cfg["max_group_size"]:
            continue
        runner_up = max(stars.get(i, 0) for i in members)
        if stars.get(keep, 0) > cfg["keep_star_ratio"] * runner_up:
            continue
        for repo_id in (keep, *members):
            result[repo_id] = ("boilerplate", None)


def run(store: Store, cfg: dict, excluded: dict[int, str] | None = None) -> dict[str, int]:
    stars = dict(store.db.execute("SELECT id, COALESCE(stars, 0) FROM repos"))
    result: dict[int, tuple[str, int | None]] = {i: ("excluded", None) for i in excluded or {} if i in stars}
    for repo_id, status in flagged(store).items():
        result.setdefault(repo_id, (status, None))
    result.update(non_english(store, result, cfg["min_english_ratio"]))
    docs = {}
    for repo_id, text, n_tokens in store.iter_rows("SELECT repo_id, text, n_tokens FROM tokens"):
        if repo_id in result:
            continue
        if n_tokens < cfg["min_tokens"]:
            result[repo_id] = ("short", None)
            continue
        docs[repo_id] = [w for sentence in deserialize(text) for w in sentence]
    for repo_id in stars:
        if repo_id not in result and repo_id not in docs:
            result[repo_id] = ("empty", None)

    exact = defaultdict(list)
    for repo_id, words in docs.items():
        exact[hashlib.sha256(" ".join(words).encode()).hexdigest()].append(repo_id)
    resolve(list(exact.values()), stars, "exact", result)
    remaining = {i: w for i, w in docs.items() if i not in result}
    resolve(near_groups(remaining, cfg), stars, "near", result)
    for repo_id in docs:
        result.setdefault(repo_id, ("kept", None))
    boilerplate(result, stars, cfg)

    store.replace_all("dedup", ("repo_id", "status", "kept_id"),
                      ((i, s, k) for i, (s, k) in sorted(result.items())))
    counts = defaultdict(int)
    for status, _ in result.values():
        counts[status] += 1
    log.info("dedup: %s", dict(counts))
    return dict(counts)
