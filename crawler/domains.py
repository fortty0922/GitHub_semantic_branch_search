import tomllib
from dataclasses import replace
from pathlib import Path

from .crawl import DEFAULT_TIERS, Tier


def load(config_dir: str | Path) -> tuple[dict[str, list[str]], int]:
    root = Path(config_dir)
    with open(root / "domains.toml", "rb") as f:
        domains = {name: entry["queries"] for name, entry in tomllib.load(f).items()}
    with open(root / "experiment.toml", "rb") as f:
        target = tomllib.load(f)["crawler"]["domain"]["target_per_domain"]
    return domains, target


def domain_tiers(domains: dict[str, list[str]], base: tuple[Tier, ...] = DEFAULT_TIERS) -> tuple[Tier, ...]:
    """Star tiers stay the outer priority; within a star tier every domain and query takes its turn."""
    return tuple(replace(t, qualifier=q, domain=d) for t in base for d in domains for q in domains[d])
