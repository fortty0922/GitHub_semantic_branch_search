import tomllib
from pathlib import Path

FILES = ("experiment", "domains", "topic_map", "awesome_lists")


class MissingConfigError(FileNotFoundError):
    pass


def read(path: Path) -> dict:
    if not path.exists():
        raise MissingConfigError(
            f"{path} not found; copy {path.name}.example next to it (init.bat / init.sh does this)")
    with open(path, "rb") as f:
        return tomllib.load(f)


def load(config_dir: str | Path = "config") -> dict:
    root = Path(config_dir)
    settings = {"config_dir": str(root)}
    for name in FILES:
        settings[name] = read(root / f"{name}.toml")
    return settings
