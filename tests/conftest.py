import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))


@pytest.fixture
def manifest():
    manifest_path = ROOT / "herdr-plugin.toml"
    with manifest_path.open("rb") as fh:
        return tomllib.load(fh)
