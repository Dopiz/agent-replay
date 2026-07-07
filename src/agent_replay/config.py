"""Read the optional ~/.agent-replay/config.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        with config_path.open("rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def auto_report_enabled(config_path: Path) -> bool:
    config = load_config(config_path)
    return config.get("auto_report", True) is not False
