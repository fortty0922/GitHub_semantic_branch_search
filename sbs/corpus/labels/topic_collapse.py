from datasketch import MinHash, MinHashLSH


def collapse_near(sets: list[frozenset[str]], cfg: dict) -> tuple[list[frozenset[str]], int]:
    """Keep one topic set per group of near-identical sets (Jaccard >= `collapse_threshold`).

    Only sets with at least `collapse_min_topics` topics take part: spam campaigns stuff long,
    slightly varied topic lists into hundreds of repositories, while short lists are naturally
    similar. A set is dropped when it is similar to a set already kept. Returns the kept sets and
    the number dropped.
    """
    big = [i for i, tags in enumerate(sets) if len(tags) >= cfg["collapse_min_topics"]]
    if not big:
        return sets, 0
    num_perm = cfg["collapse_num_perm"]
    hashes = MinHash.bulk([[t.encode() for t in sorted(sets[i])] for i in big], num_perm=num_perm)
    lsh = MinHashLSH(threshold=cfg["collapse_threshold"], num_perm=num_perm)
    dropped = set()
    for i, mh in zip(big, hashes):
        if lsh.query(mh):
            dropped.add(i)
        else:
            lsh.insert(i, mh)
    return [tags for i, tags in enumerate(sets) if i not in dropped], len(dropped)
