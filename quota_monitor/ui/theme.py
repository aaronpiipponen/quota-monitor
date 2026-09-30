"""Colours and display names. Adjust freely; nothing else depends on the exact values."""

from __future__ import annotations

BACKGROUND = "#2B2D31"
TEXT = "#E6E6E6"
MUTED = "#A3A7AE"
DANGER = "#F2545B"
WARN = "#E8B04A"
OK = "#3CC47C"
BUTTON_HOVER = "#3A3D43"

_PROVIDERS = {
    "claude": ("Claude", "#E07A4F"),
    "codex": ("Codex", "#3B9EEB"),
    "antigravity": ("Antigravity", "#2BC4A0"),
    "commandcode": ("Command Code", "#A78BFA"),
    "deepseek": ("DeepSeek", "#5B7CFA"),
    "opencode_go": ("OpenCode Go", "#E8E8E8"),
}
_DEFAULT_COLOR = "#9AA0A6"


def provider_style(key: str) -> tuple[str, str]:
    """Return (display name, accent colour) for a provider key."""
    return _PROVIDERS.get(key, (key.replace("_", " ").title(), _DEFAULT_COLOR))


def severity_color(level: str) -> str:
    return {"danger": DANGER, "warn": WARN}.get(level, OK)
