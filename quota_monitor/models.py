"""Core data structures shared by providers, storage and presentation layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# A meter either expresses consumption of a window as a percentage
# (subscription-style limits) or an absolute remaining amount (prepaid credit).
KIND_PERCENT_USED = "percent_used"
KIND_BALANCE = "balance"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Meter:
    """A single measured quantity reported by a provider."""

    provider: str
    name: str
    kind: str
    value: float
    unit: str
    limit: Optional[float] = None
    resets_at: Optional[datetime] = None
    # Display text such as "5h" or "Fable weekly". Falls back to ``name`` when empty.
    label: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.name


@dataclass
class PollResult:
    """Outcome of polling one provider once. Either meters or an error is set."""

    provider: str
    polled_at: datetime
    meters: list[Meter] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None
