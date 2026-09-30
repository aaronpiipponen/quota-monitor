"""OpenCode Go provider: 5-hour, weekly and monthly subscription limits.

Data source: the endpoint the OpenCode console uses for its Go page,
``GET https://opencode.ai/console/api/go/status`` (undocumented), authenticated with
the console's session cookies and the ``X-Org-Id`` header (the ``org_...`` id from
the console URL). Each meter reports a limit and the amount used
in micro-cents, plus its reset time. A 5-hour window that has not started yet has no
reset time and zero usage.
"""

from __future__ import annotations

import re
from typing import Any

from ..http import HttpError
from ..models import KIND_PERCENT_USED, Meter
from .base import Provider, ProviderError, parse_timestamp, to_float

STATUS_URL = "https://opencode.ai/console/api/go/status"
_METERS = (("fiveHour", "five_hour", "5h"), ("week", "weekly", "Weekly"), ("month", "monthly", "Monthly"))
EXPIRED = "OpenCode session has expired. Copy a fresh Cookie value into OPENCODE_COOKIE in .env."


def org_id(value: str) -> str:
    """Accept either an org id or a console URL containing it."""
    match = re.search(r"org_[A-Za-z0-9]+", value)
    if not match:
        raise ProviderError("OPENCODE_ORG must be the org_... id or the console URL containing it.")
    return match.group(0)


def normalize_cookie(raw: str) -> str:
    value = raw.strip().strip('"').strip("'")
    if value.lower().startswith("cookie:"):
        value = value[len("cookie:"):].strip()
    return value if value.startswith("auth=") or ";" in value else f"auth={value}"


def parse_status(data: Any) -> list[Meter]:
    access = data.get("access") if isinstance(data, dict) else None
    meters_data = access.get("meters") if isinstance(access, dict) else None
    if not isinstance(meters_data, dict):
        raise ProviderError("No active OpenCode Go subscription found for this account.")
    meters = []
    for key, name, label in _METERS:
        meter = meters_data.get(key)
        if not isinstance(meter, dict):
            continue
        limit = to_float(meter.get("limitMicroCents") or 0, f"{label} limit")
        used = to_float(meter.get("usedMicroCents") or 0, f"{label} usage")
        if limit <= 0:
            continue
        meters.append(Meter(provider="opencode_go", name=name, kind=KIND_PERCENT_USED,
                            value=max(0.0, min(100.0, used / limit * 100)), unit="%",
                            resets_at=parse_timestamp(meter.get("resetsAt")), label=label))
    return meters


class OpenCodeGoProvider(Provider):
    name = "opencode_go"

    def fetch(self) -> list[Meter]:
        cookie = normalize_cookie(self.require_secret("cookie"))
        org = org_id(self.require_secret("org"))
        try:
            data = self.http_get(STATUS_URL, headers={"Cookie": cookie, "X-Org-Id": org})
        except HttpError as exc:
            # An expired session is answered with 401/403 or redirected to the HTML login page.
            if exc.status in (401, 403) or (exc.status is None and "not valid JSON" in str(exc)):
                raise ProviderError(EXPIRED) from exc
            detail = f" OpenCode says: {exc.body.strip()[:160]}" if exc.body.strip() else ""
            raise ProviderError(f"{exc}.{detail}") from exc
        meters = parse_status(data)
        if not meters:
            raise ProviderError("The Go status response contained no usage meters.")
        return meters
