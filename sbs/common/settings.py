import tomllib
from pathlib import Path

FILES = ("experiment", "topic_map", "awesome_lists")


def load(config_dir: str | Path = "config") -> dict:
    root = Path(config_dir)
    settings = {}
    for name in FILES:
        with open(root / f"{name}.toml", "rb") as f:
            settings[name] = tomllib.load(f)
    return settings
