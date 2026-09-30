"""OpenCode Go provider: 5-hour, weekly and monthly subscription limits.

OpenCode has no usage API. The workspace's Go page on opencode.ai is rendered on the
server with the usage data embedded in the HTML, so this provider downloads that page
with the console login cookie and reads the three usage windows from it.

Each window in the page data has the form
    rollingUsage: {status: "ok", resetInSec: 1234, usagePercent: 20, usage: ..., limit: ...}
(also weeklyUsage and monthlyUsage). The data is embedded as JavaScript, so keys may or
may not be quoted; the parser accepts both.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from ..http import HttpError
from ..models import KIND_PERCENT_USED, Meter, utc_now
from .base import Provider, ProviderError

BASE_URL = "https://opencode.ai"
_WINDOWS = (("rollingUsage", "five_hour", "5h"), ("weeklyUsage", "weekly", "Weekly"),
            ("monthlyUsage", "monthly", "Monthly"))


def workspace_id(value: str) -> str:
    """Accept either a workspace id or a console URL containing /workspace/<id>."""
    value = value.strip()
    match = re.search(r"/workspace/([^/?#]+)", value)
    workspace = match.group(1) if match else value
    if not workspace or "/" in workspace:
        raise ProviderError("OPENCODE_WORKSPACE must be the workspace id or its console URL.")
    return workspace


def _number(block: str, key: str) -> Optional[float]:
    match = re.search(rf"""["']?{key}["']?\s*:\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)""", block)
    return float(match.group(1)) if match else None


def parse_page(html: str, now=None) -> list[Meter]:
    now = now or utc_now()
    meters: list[Meter] = []
    for key, name, label in _WINDOWS:
        # The window object contains no nested braces, so its body ends at the first '}'.
        match = re.search(rf"""["']?{key}["']?\s*:\s*\{{([^{{}}]*)\}}""", html)
        if not match:
            continue
        percent = _number(match.group(1), "usagePercent")
        if percent is None:
            continue
        reset_in = _number(match.group(1), "resetInSec")
        meters.append(Meter(provider="opencode_go", name=name, kind=KIND_PERCENT_USED,
                            value=max(0.0, min(100.0, percent)), unit="%",
                            resets_at=now + timedelta(seconds=reset_in) if reset_in is not None else None,
                            label=label))
    return meters


def normalize_cookie(raw: str) -> str:
    value = raw.strip().strip('"').strip("'")
    if value.lower().startswith("cookie:"):
        value = value[len("cookie:"):].strip()
    return value if "=" in value else f"auth={value}"  # The console session cookie is named 'auth'.


class OpenCodeGoProvider(Provider):
    name = "opencode_go"

    def fetch(self) -> list[Meter]:
        cookie = normalize_cookie(self.require_secret("cookie"))
        workspace = workspace_id(self.require_secret("workspace"))
        url = f"{BASE_URL}/workspace/{workspace}/go"
        try:
            html = self.http_get_text(url, headers={"Cookie": cookie})
        except HttpError as exc:
            if exc.status in (401, 403):
                raise ProviderError("OpenCode session has expired. Copy a fresh 'auth' cookie "
                                    "into OPENCODE_COOKIE in .env.") from exc
            raise ProviderError(str(exc)) from exc
        meters = parse_page(html)
        if meters:
            return meters
        if "rollingUsage" not in html and "usagePercent" not in html:
            raise ProviderError("No Go usage found on the page. The OpenCode session may have expired "
                                "(copy a fresh cookie into OPENCODE_COOKIE), the workspace may have no "
                                "Go subscription, or OpenCode changed the page.")
        raise ProviderError("Could not read the Go usage from the page; OpenCode may have changed its format.")
