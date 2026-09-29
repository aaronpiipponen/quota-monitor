"""Command Code provider: 5-hour and weekly rolling limits plus the monthly credit grant.

Data source: Command Code's web billing API (undocumented; request format follows
CodexBar's implementation, MIT licence). It authenticates with the commandcode.ai
website's session cookie. Browsers encrypt their cookie stores, so the cookie is
copied once from the browser's developer tools into .env (see README).

The session is a server-side login session: polling it counts as activity, so a
copied cookie normally stays valid as long as the monitor keeps running. Signing
out on the website invalidates it.
"""

from __future__ import annotations

from typing import Any, Optional

from ..http import HttpError
from ..models import KIND_BALANCE, KIND_PERCENT_USED, Meter
from .base import Provider, ProviderError, parse_timestamp, to_float

API_BASE = "https://api.commandcode.ai"
CREDITS_URL = f"{API_BASE}/internal/billing/credits"
SUBSCRIPTIONS_URL = f"{API_BASE}/internal/billing/subscriptions"
WEB_ORIGIN = "https://commandcode.ai"
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def normalize_cookie(raw: str) -> str:
    """Accept either a bare 'name=value; ...' string or a pasted 'Cookie: ...' header line."""
    value = raw.strip().strip('"').strip("'")
    if value.lower().startswith("cookie:"):
        value = value[len("cookie:"):].strip()
    if "=" not in value:
        raise ProviderError("COMMANDCODE_COOKIE must contain 'name=value' pairs. Copy the whole "
                            "Cookie header from the browser's developer tools (see README).")
    return value


def _percent(used: Any, cap: Any) -> Optional[float]:
    try:
        used_f, cap_f = float(used or 0), float(cap)
    except (TypeError, ValueError):
        return None
    if cap_f <= 0:
        return None
    return max(0.0, min(100.0, used_f / cap_f * 100))


def parse_usage(credits_data: Any, subscription_data: Any = None) -> list[Meter]:
    if not isinstance(credits_data, dict) or not isinstance(credits_data.get("credits"), dict):
        raise ProviderError("Unexpected credits response shape (missing 'credits').")
    credits = credits_data["credits"]
    if credits.get("monthlyCredits") is None:
        raise ProviderError("Credits response is missing 'monthlyCredits'.")
    remaining = to_float(credits["monthlyCredits"], "monthlyCredits")
    windows = credits_data.get("windowLimits") or credits.get("windowLimits") or {}

    meters: list[Meter] = []
    for key, name, label in (("fiveHour", "five_hour", "5h"), ("weekly", "weekly", "Weekly")):
        window = windows.get(key) if isinstance(windows, dict) else None
        if isinstance(window, dict):
            percent = _percent(window.get("used"), window.get("cap"))
            if percent is not None:
                meters.append(Meter(provider="commandcode", name=name, kind=KIND_PERCENT_USED,
                                    value=percent, unit="%",
                                    resets_at=parse_timestamp(window.get("resetAt")), label=label))

    period_end = None
    if isinstance(subscription_data, dict) and isinstance(subscription_data.get("data"), dict):
        period_end = parse_timestamp(subscription_data["data"].get("currentPeriodEnd"))

    granted = credits.get("monthlyCreditsGranted")
    granted_f = to_float(granted, "monthlyCreditsGranted") if granted is not None else 0.0
    if granted_f > 0:
        used = max(0.0, min(granted_f, granted_f - remaining))
        meters.append(Meter(provider="commandcode", name="monthly", kind=KIND_PERCENT_USED,
                            value=used / granted_f * 100, unit="%", limit=None,
                            resets_at=period_end, label="Monthly"))
    else:
        meters.append(Meter(provider="commandcode", name="monthly_remaining", kind=KIND_BALANCE,
                            value=remaining, unit="USD", resets_at=period_end,
                            label="Monthly credits left"))

    purchased = credits.get("purchasedCredits")
    if purchased is not None and to_float(purchased, "purchasedCredits") > 0:
        meters.append(Meter(provider="commandcode", name="purchased", kind=KIND_BALANCE,
                            value=to_float(purchased, "purchasedCredits"), unit="USD",
                            label="Top-up credits"))
    return meters


class CommandCodeProvider(Provider):
    name = "commandcode"

    def fetch(self) -> list[Meter]:
        cookie = normalize_cookie(self.require_secret("cookie"))
        headers = {"Cookie": cookie, "Origin": WEB_ORIGIN, "Referer": f"{WEB_ORIGIN}/",
                   "User-Agent": BROWSER_USER_AGENT, "Accept": "application/json, text/plain, */*"}
        try:
            credits = self.http_get(CREDITS_URL, headers=headers)
        except HttpError as exc:
            if exc.status in (401, 403):
                raise ProviderError("Command Code session has expired. Copy a fresh cookie from "
                                    "commandcode.ai into COMMANDCODE_COOKIE in .env.") from exc
            raise ProviderError(str(exc)) from exc
        # The subscription lookup only adds the monthly reset date, so its failure is tolerated.
        try:
            subscription = self.http_get(SUBSCRIPTIONS_URL, headers=headers)
        except HttpError:
            subscription = None
        return parse_usage(credits, subscription)
