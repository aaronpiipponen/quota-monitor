"""Pure text-formatting helpers for the UI. Deliberately free of Qt imports so they
can be unit-tested on any machine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..models import KIND_PERCENT_USED

WARN_PERCENT = 70.0
DANGER_PERCENT = 90.0


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; naive values are assumed to be UTC."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def format_duration(seconds: float) -> str:
    """Render a duration using its two most significant units, e.g. '1 day, 10 hours'.

    The second unit is omitted when it is zero ('5 days' rather than '5 days, 0 hours').
    """
    if seconds < 60:
        return "under a minute"
    minutes = int(seconds // 60)
    days, remainder = divmod(minutes, 24 * 60)
    hours, mins = divmod(remainder, 60)
    units = [(days, "day"), (hours, "hour"), (mins, "minute")]
    first = next(i for i, (value, _) in enumerate(units) if value > 0)
    shown = [units[first]]
    if first + 1 < len(units) and units[first + 1][0] > 0:
        shown.append(units[first + 1])
    return ", ".join(_plural(value, unit) for value, unit in shown)


def format_reset(resets_at: Optional[datetime], now: datetime) -> Optional[str]:
    if resets_at is None:
        return None
    remaining = (resets_at - now).total_seconds()
    if remaining <= 0:
        return "Reset due now"
    return f"Resets in {format_duration(remaining)}"


def format_ago(moment: Optional[datetime], now: datetime) -> str:
    if moment is None:
        return "never"
    elapsed = (now - moment).total_seconds()
    return "just now" if elapsed < 60 else f"{format_duration(elapsed)} ago"


def format_meter_value(meter: dict[str, Any]) -> str:
    if meter["kind"] == KIND_PERCENT_USED:
        return f"{round(meter['value'])}% used"
    return f"{meter['value']:,.2f} {meter['unit']}"


def severity(percent: Optional[float]) -> str:
    """Classify a usage percentage as 'ok', 'warn' or 'danger'."""
    if percent is None or percent < WARN_PERCENT:
        return "ok"
    return "danger" if percent >= DANGER_PERCENT else "warn"


def highest_usage(rows: list[dict[str, Any]]) -> Optional[tuple[float, str, str]]:
    """Return (percent, provider, meter label) for the most-used window, if any."""
    best = None
    for row in rows:
        for meter in row.get("meters", []):
            if meter["kind"] == KIND_PERCENT_USED and (best is None or meter["value"] > best[0]):
                best = (meter["value"], row["provider"], meter.get("label") or meter["meter"])
    return best
