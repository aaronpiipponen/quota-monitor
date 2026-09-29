"""Configuration loading from a TOML file."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):
    raise SystemExit("Quota Monitor requires Python 3.11 or newer (for the built-in tomllib).")

import tomllib


@dataclass
class AppConfig:
    db_path: Path
    poll_interval_minutes: int
    providers: dict[str, dict[str, Any]]


def load_config(path: Path) -> AppConfig:
    """Load and validate the config file. Relative paths resolve against its directory."""
    if not path.is_file():
        raise SystemExit(f"Config file not found: {path}. Copy config.example.toml to get started.")
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    general = raw.get("general", {})
    db_path = Path(general.get("database", "data/quota.db"))
    if not db_path.is_absolute():
        db_path = path.parent / db_path

    interval = int(general.get("poll_interval_minutes", 10))
    if interval < 1:
        raise SystemExit("poll_interval_minutes must be at least 1.")

    providers = raw.get("providers", {})
    if not isinstance(providers, dict):
        raise SystemExit("[providers] must be a table of provider sections.")

    return AppConfig(db_path=db_path, poll_interval_minutes=interval, providers=providers)
