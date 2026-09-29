"""Fake status rows for designing the UI without real credentials.

The rows use exactly the same shape as Storage.latest_status(), so the panel
cannot tell demo data from real data.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..models import KIND_BALANCE, KIND_PERCENT_USED, utc_now


def _pct(meter: str, label: str, value: float, resets_in: timedelta) -> dict[str, Any]:
    return {"meter": meter, "label": label, "kind": KIND_PERCENT_USED, "value": value,
            "unit": "%", "limit_value": None, "resets_at": (utc_now() + resets_in).isoformat()}


def _bal(meter: str, label: str, value: float, unit: str) -> dict[str, Any]:
    return {"meter": meter, "label": label, "kind": KIND_BALANCE, "value": value,
            "unit": unit, "limit_value": None, "resets_at": None}


def demo_status() -> list[dict[str, Any]]:
    now = utc_now()
    recent = (now - timedelta(minutes=2)).isoformat()

    def row(provider, meters, error=None, last_ok=recent):
        return {"provider": provider, "last_polled_at": recent, "ok": error is None,
                "error": error, "last_ok_at": last_ok, "meters": meters}

    return [
        row("claude", [
            _pct("five_hour", "5h", 20, timedelta(hours=1, minutes=24)),
            _pct("seven_day", "Weekly", 48, timedelta(days=1, hours=10)),
            _pct("seven_day_fable", "Fable weekly", 11, timedelta(days=1, hours=10)),
        ]),
        row("codex", [
            _pct("primary", "5h", 54, timedelta(hours=3, minutes=5)),
            _pct("secondary", "Weekly", 93, timedelta(days=5)),
        ]),
        row("antigravity", [
            _pct("gemini_5h", "Gemini 5h", 0, timedelta(hours=4, minutes=54)),
            _pct("gemini_weekly", "Gemini weekly", 33, timedelta(days=5)),
            _pct("claude_gpt_5h", "Claude/GPT 5h", 0, timedelta(hours=4, minutes=54)),
            _pct("claude_gpt_weekly", "Claude/GPT weekly", 0, timedelta(days=6, hours=23)),
        ]),
        row("commandcode", [
            _pct("five_hour", "5h", 35, timedelta(hours=2, minutes=10)),
            _pct("weekly", "Weekly", 41, timedelta(days=3, hours=6)),
            _pct("monthly", "Monthly", 72, timedelta(days=12, hours=3)),
        ], error="HTTP 503 from api.commandcode.ai",
            last_ok=(now - timedelta(minutes=25)).isoformat()),
        row("deepseek", [
            _bal("balance_usd", "Balance", 2.92, "USD"),
        ]),
    ]
