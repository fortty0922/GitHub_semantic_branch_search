import tomllib
from dataclasses import replace
from pathlib import Path

from .crawl import DEFAULT_TIERS, Tier


def read(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; copy {path.name}.example next to it (init.bat / init.sh does this)")
    with open(path, "rb") as f:
        return tomllib.load(f)


def queries(domains: dict, topic_map: dict, max_topics: int) -> dict[str, list[str]]:
    """Per domain: `topic:` queries for the most frequent topics of topic_map.toml (seeds when the
    domain is missing there), then the extra queries of domains.toml."""
    out = {}
    for name, entry in domains.items():
        topics = topic_map.get(name, {}).get("topics") or entry.get("seed_topics", [])
        qs = [f"topic:{t}" for t in topics[:max_topics]] + list(entry.get("queries", []))
        out[name] = list(dict.fromkeys(qs))
    return out


def load(config_dir: str | Path) -> tuple[dict[str, list[str]], int]:
    root = Path(config_dir)
    cfg = read(root / "experiment.toml")["crawler"]["domain"]
    return queries(read(root / "domains.toml"), read(root / "topic_map.toml"), cfg["max_topic_queries"]), \
        cfg["target_per_domain"]


def domain_tiers(domains: dict[str, list[str]], base: tuple[Tier, ...] = DEFAULT_TIERS) -> tuple[Tier, ...]:
    """Star tiers stay the outer priority; within a star tier every domain and query takes its turn."""
    return tuple(replace(t, qualifier=q, domain=d) for t in base for d in domains for q in domains[d])
