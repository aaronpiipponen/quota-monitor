"""Claude provider: subscription usage windows (5h, weekly, per-model weekly).

Sign-in: Claude account login in the browser (tray menu → Sign in to Claude).
Usage: the OAuth usage endpoint used by Claude Code (undocumented).
Tokens are kept in data/auth/claude.json and renewed automatically.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ..auth import LoginError, TokenStore, browser_login, now_ms
from ..http import HttpError, post_json
from ..models import KIND_PERCENT_USED, Meter
from .base import Provider, ProviderError, parse_timestamp, to_float

AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
BETA_HEADER = "oauth-2025-04-20"
USER_AGENT = "claude-code/2.1.0"
EXPIRY_MARGIN_MS = 60_000
NOT_SIGNED_IN = "Not signed in. Use 'Sign in to Claude' in the tray menu."

_FIXED_WINDOWS = (
    ("five_hour", "5h"),
    ("seven_day", "Weekly"),
    ("seven_day_opus", "Opus weekly"),
    ("seven_day_sonnet", "Sonnet weekly"),
)


def _tokens_from_response(data: Any, previous_refresh: str = "") -> dict[str, Any]:
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ProviderError("The token response was not recognised.")
    return {"access": data["access_token"],
            "refresh": data.get("refresh_token") or previous_refresh,
            "expires_ms": now_ms() + int(data.get("expires_in") or 3600) * 1000}


def login(state_dir, http_post_json=post_json, open_browser=None) -> str:
    """Sign in through the browser and store the tokens. Returns a status message."""
    def build(redirect: str, challenge: str, state: str) -> str:
        return AUTHORIZE_URL + "?" + urlencode({
            "code": "true", "client_id": CLIENT_ID, "response_type": "code",
            "redirect_uri": redirect, "scope": SCOPES, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state})

    kwargs = {"open_browser": open_browser} if open_browser else {}
    result = browser_login(build, port=0, path="/callback", **kwargs)
    try:
        data = http_post_json(TOKEN_URL, {
            "grant_type": "authorization_code", "code": result.code, "state": result.state,
            "redirect_uri": result.redirect_uri, "client_id": CLIENT_ID,
            "code_verifier": result.verifier})
    except HttpError as exc:
        raise LoginError(f"Claude did not accept the sign-in: {exc}") from exc
    TokenStore(state_dir, "claude").save(_tokens_from_response(data))
    return "Signed in to Claude."


def parse_usage(data: Any) -> list[Meter]:
    """Map the usage response to meters. Unknown or empty windows are skipped."""
    if not isinstance(data, dict):
        raise ProviderError("Unexpected usage response shape.")
    meters: list[Meter] = []
    seen_labels: set[str] = set()

    def add(name: str, label: str, utilization: Any, resets_at: Any) -> None:
        if utilization is None or label.lower() in seen_labels:
            return
        seen_labels.add(label.lower())
        meters.append(Meter(provider="claude", name=name, kind=KIND_PERCENT_USED,
                            value=to_float(utilization, label), unit="%",
                            resets_at=parse_timestamp(resets_at), label=label))

    # Model-scoped weekly limits from 'limits' take precedence over the flat fields.
    scoped = []
    for entry in data.get("limits") or []:
        if not isinstance(entry, dict) or entry.get("group") != "weekly" \
                or entry.get("kind") != "weekly_scoped":
            continue
        model_name = str(((entry.get("scope") or {}).get("model") or {}).get("display_name") or "").strip()
        if model_name and model_name.lower().replace(" ", "-") != "all-models":
            scoped.append((model_name, entry))

    for key, label in _FIXED_WINDOWS[:2]:
        window = data.get(key)
        if isinstance(window, dict):
            add(key, label, window.get("utilization"), window.get("resets_at"))
    for model_name, entry in scoped:
        slug = "".join(c if c.isalnum() else "_" for c in model_name.lower())
        add(f"weekly_{slug}", f"{model_name} weekly", entry.get("percent"), entry.get("resets_at"))
    for key, label in _FIXED_WINDOWS[2:]:
        window = data.get(key)
        if isinstance(window, dict):
            add(key, label, window.get("utilization"), window.get("resets_at"))

    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        percent = extra.get("utilization")
        if percent is None and extra.get("monthly_limit"):
            percent = to_float(extra.get("used_credits") or 0, "extra usage") \
                / to_float(extra["monthly_limit"], "extra usage") * 100
        add("extra_usage", "Extra usage", percent, None)
    return meters


class ClaudeProvider(Provider):
    name = "claude"

    def _store(self) -> TokenStore:
        return TokenStore(self.state_dir, "claude")

    def _refresh(self, tokens: dict[str, Any]) -> dict[str, Any]:
        if not tokens.get("refresh"):
            raise ProviderError("Claude sign-in has expired. " + NOT_SIGNED_IN.split(". ", 1)[1])
        try:
            data = self.http_post(TOKEN_URL, {"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh"], "client_id": CLIENT_ID})
        except HttpError as exc:
            if "invalid_grant" in exc.body:
                raise ProviderError("Claude sign-in is no longer valid. Use 'Sign in to Claude' "
                                    "in the tray menu.") from exc
            raise ProviderError(f"Token renewal failed: {exc}") from exc
        renewed = _tokens_from_response(data, tokens["refresh"])
        self._store().save(renewed)  # Refresh tokens rotate, so the new one must be kept.
        return renewed

    def _get_usage(self, access: str) -> Any:
        return self.http_get(USAGE_URL, headers={"Authorization": f"Bearer {access}",
                                                 "anthropic-beta": BETA_HEADER, "User-Agent": USER_AGENT})

    def fetch(self) -> list[Meter]:
        tokens = self._store().load()
        if not tokens or not tokens.get("access"):
            raise ProviderError(NOT_SIGNED_IN)
        if int(tokens.get("expires_ms", 0)) - EXPIRY_MARGIN_MS <= now_ms():
            tokens = self._refresh(tokens)
        try:
            data = self._get_usage(tokens["access"])
        except HttpError as exc:
            if exc.status == 401:  # Revoked early; renew once and retry.
                try:
                    data = self._get_usage(self._refresh(tokens)["access"])
                except HttpError as retry_exc:
                    raise ProviderError(f"Claude rejected the sign-in ({retry_exc}). Use 'Sign in "
                                        "to Claude' in the tray menu.") from retry_exc
            elif exc.status == 429:
                raise ProviderError("Claude is rate-limiting usage requests; retrying next poll.") from exc
            else:
                raise ProviderError(str(exc)) from exc
        meters = parse_usage(data)
        if not meters:
            raise ProviderError("The usage response contained no usage windows.")
        return meters
