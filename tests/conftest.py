import shutil
from pathlib import Path

import pytest


@pytest.fixture
def example_config(tmp_path):
    """A config directory built from the committed config/*.toml.example files."""
    root = tmp_path / "config"
    root.mkdir()
    for example in Path("config").glob("*.toml.example"):
        shutil.copy(example, root / example.name.removesuffix(".example"))
    return root
