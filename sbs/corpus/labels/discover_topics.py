import logging
from pathlib import Path

from sbs.common.store import Store
from sbs.corpus.labels import discovery, topic_map_file

log = logging.getLogger("sbs")


def run(store: Store, settings: dict) -> dict[str, int]:
    """Grow the seed topics of domains.toml over every repository in the database and write
    topic_map.toml in the config directory. Meant for a database from a general (non-domain) crawl."""
    seeds = {d: e["seed_topics"] for d, e in settings["domains"].items()}
    sql = "SELECT topics FROM repos"
    if store.has_column("repos", "fork"):
        sql += " WHERE fork IS NOT 1"
    repos = discovery.topic_sets(t for (t,) in store.iter_rows(sql))
    found = discovery.discover(repos, seeds, settings["experiment"]["corpus"]["topics"])
    store.replace_all("topic_discovery", ("topic", "domain", "round", "count", "assoc"),
                      ((t, e["domain"], e["round"], e["count"], e["assoc"]) for t, e in sorted(found.items())))
    topics = discovery.by_domain(found, list(seeds))
    path = Path(settings["config_dir"]) / "topic_map.toml"
    topic_map_file.write(path, topics)
    counts = {d: len(ts) for d, ts in topics.items()}
    log.info("discover-topics: %d repositories with topics, topics per domain %s, written to %s",
             len(repos), counts, path)
    return counts
