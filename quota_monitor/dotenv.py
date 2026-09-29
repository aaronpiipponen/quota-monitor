"""Minimal .env file loader.

Supports the common subset of the format:
    KEY=value
    KEY="value with spaces"      # quotes are stripped
    KEY='literal value'
    export KEY=value             # optional 'export' prefix (shell-compatible)
    # full-line comments and blank lines are ignored
    KEY=value # inline comment   # only for unquoted values

Variables already present in the real environment are NOT overwritten. This
follows the usual dotenv convention: the .env file supplies defaults, and an
explicitly set environment variable always wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DotenvError(Exception):
    """Raised for malformed lines, reported with the line number."""


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY_PATTERN.match(key):
            raise DotenvError(f".env line {number}: expected KEY=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Strip inline comments from unquoted values ("abc # note" -> "abc").
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_dotenv(path: Path) -> list[str]:
    """Load variables from ``path`` into os.environ. Returns the names that were set.

    A missing file is not an error, because environment variables alone are a
    valid way to configure the application.
    """
    if not path.is_file():
        return []
    # utf-8-sig transparently removes a byte-order mark, which some Windows
    # editors add and which would otherwise corrupt the first key name.
    values = parse_dotenv(path.read_text(encoding="utf-8-sig"))
    applied = []
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied
