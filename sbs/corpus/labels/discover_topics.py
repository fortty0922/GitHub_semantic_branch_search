import logging
from pathlib import Path

from sbs.common.store import Store
from sbs.corpus.labels import discovery, topic_map_file

log = logging.getLogger("sbs")


def background_sql(store: Store) -> str:
    """Only repositories that dedup kept, when the corpus stage has run on this database (spam,
    template, non-English and duplicate repositories otherwise inflate their shared topics)."""
    if store.db.execute("SELECT 1 FROM dedup LIMIT 1").fetchone():
        log.info("discover-topics: background = repositories kept by dedup")
        return "SELECT r.topics FROM repos r JOIN dedup d ON d.repo_id = r.id WHERE d.status = 'kept'"
    log.info("discover-topics: dedup has not run, background = all repositories")
    sql = "SELECT topics FROM repos"
    return sql + " WHERE fork IS NOT 1" if store.has_column("repos", "fork") else sql


def run(store: Store, settings: dict) -> dict[str, int]:
    """Grow the seed topics of domains.toml over the repositories of the database and write
    topic_map.toml in the config directory. Meant for a database from a general (non-domain) crawl."""
    seeds = {d: e["seed_topics"] for d, e in settings["domains"].items()}
    repos = discovery.topic_sets(t for (t,) in store.iter_rows(background_sql(store)))
    found = discovery.discover(repos, seeds, settings["experiment"]["corpus"]["topics"])
    store.reset("topic_discovery")
    store.replace_all("topic_discovery", ("topic", "domain", "round", "count", "assoc", "lift", "seed_assoc"),
                      ((t, e["domain"], e["round"], e["count"], e["assoc"], e["lift"], e["seed_assoc"])
                       for t, e in sorted(found.items())))
    topics = discovery.by_domain(found, list(seeds))
    path = Path(settings["config_dir"]) / "topic_map.toml"
    topic_map_file.write(path, topics)
    counts = {d: len(ts) for d, ts in topics.items()}
    log.info("discover-topics: %d repositories with topics, topics per domain %s, written to %s",
             len(repos), counts, path)
    return counts
