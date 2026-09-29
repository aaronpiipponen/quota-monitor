"""Codex provider: ChatGPT subscription limits for Codex (5h, weekly, model-specific).

Sign-in: ChatGPT account login in the browser (tray menu → Sign in to Codex).
Usage: the usage endpoint used by the Codex CLI (undocumented).
Tokens are kept in data/auth/codex.json and renewed automatically.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from ..auth import LoginError, TokenStore, browser_login
from ..http import HttpError, post_form
from ..models import KIND_BALANCE, KIND_PERCENT_USED, Meter
from .base import Provider, ProviderError, jwt_claims, parse_timestamp, to_float

ISSUER = "https://auth.openai.com"
TOKEN_URL = f"{ISSUER}/oauth/token"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CALLBACK_PORT = 1455  # The only redirect port registered for this client.
EXPIRY_MARGIN_S = 60
NOT_SIGNED_IN = "Not signed in. Use 'Sign in to Codex' in the tray menu."


def account_id_from_jwt(token: str) -> str:
    claims = jwt_claims(token)
    nested = claims.get("https://api.openai.com/auth")
    for candidate in (claims.get("chatgpt_account_id"),
                      nested.get("chatgpt_account_id") if isinstance(nested, dict) else None):
        if candidate:
            return str(candidate)
    return ""


def _tokens_from_response(data: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ProviderError("The token response was not recognised.")
    previous = previous or {}
    access = data["access_token"]
    id_token = data.get("id_token") or previous.get("id_token", "")
    exp = jwt_claims(access).get("exp")
    expires_s = float(exp) if isinstance(exp, (int, float)) else time.time() + int(data.get("expires_in") or 3600)
    return {"access": access, "refresh": data.get("refresh_token") or previous.get("refresh", ""),
            "id_token": id_token, "expires_s": expires_s,
            "account_id": account_id_from_jwt(id_token) or account_id_from_jwt(access)
            or previous.get("account_id", "")}


def login(state_dir, http_post=post_form, open_browser=None) -> str:
    def build(redirect: str, challenge: str, state: str) -> str:
        return f"{ISSUER}/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": redirect,
            "scope": "openid profile email offline_access", "code_challenge": challenge,
            "code_challenge_method": "S256", "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true", "state": state, "originator": "codex_cli_rs"})

    kwargs = {"open_browser": open_browser} if open_browser else {}
    result = browser_login(build, port=CALLBACK_PORT, path="/auth/callback", **kwargs)
    try:
        data = http_post(TOKEN_URL, {"grant_type": "authorization_code", "code": result.code,
                                     "redirect_uri": result.redirect_uri, "client_id": CLIENT_ID,
                                     "code_verifier": result.verifier})
    except HttpError as exc:
        raise LoginError(f"ChatGPT did not accept the sign-in: {exc}") from exc
    TokenStore(state_dir, "codex").save(_tokens_from_response(data))
    return "Signed in to Codex."


def _window_label(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "Window"
    if seconds == 7 * 86400:
        return "Weekly"
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    return f"{max(1, round(seconds / 3600))}h"


def _windows(rate_limit: Any, prefix: str, name_prefix: str) -> list[Meter]:
    meters = []
    if not isinstance(rate_limit, dict):
        return meters
    for key in ("primary_window", "secondary_window"):
        window = rate_limit.get(key)
        if not isinstance(window, dict) or window.get("used_percent") is None:
            continue
        label = _window_label(window.get("limit_window_seconds"))
        meters.append(Meter(provider="codex", name=f"{name_prefix}{key}", kind=KIND_PERCENT_USED,
                            value=to_float(window["used_percent"], label), unit="%",
                            resets_at=parse_timestamp(window.get("reset_at")),
                            label=f"{prefix}{label}"))
    return meters


def parse_usage(data: Any) -> list[Meter]:
    if not isinstance(data, dict):
        raise ProviderError("Unexpected usage response shape.")
    meters = _windows(data.get("rate_limit"), "", "")
    for extra in data.get("additional_rate_limits") or []:
        if not isinstance(extra, dict):
            continue
        name = str(extra.get("limit_name") or extra.get("metered_feature") or "").strip()
        if name:
            slug = "".join(c if c.isalnum() else "_" for c in name.lower())
            meters += _windows(extra.get("rate_limit"), f"{name} ", f"{slug}_")
    credits = data.get("credits")
    if isinstance(credits, dict) and credits.get("has_credits") and not credits.get("unlimited") \
            and credits.get("balance") is not None:
        meters.append(Meter(provider="codex", name="credits", kind=KIND_BALANCE,
                            value=to_float(credits["balance"], "credits"), unit="credits", label="Credits"))
    return meters


class CodexProvider(Provider):
    name = "codex"

    def _store(self) -> TokenStore:
        return TokenStore(self.state_dir, "codex")

    def _refresh(self, tokens: dict[str, Any]) -> dict[str, Any]:
        if not tokens.get("refresh"):
            raise ProviderError("Codex sign-in has expired. Use 'Sign in to Codex' in the tray menu.")
        try:
            data = self.http_post(TOKEN_URL, {"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh"], "client_id": CLIENT_ID})
        except HttpError as exc:
            if exc.status in (400, 401):
                raise ProviderError("Codex sign-in is no longer valid. Use 'Sign in to Codex' "
                                    "in the tray menu.") from exc
            raise ProviderError(f"Token renewal failed: {exc}") from exc
        renewed = _tokens_from_response(data, tokens)
        self._store().save(renewed)
        return renewed

    def _get_usage(self, tokens: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {tokens['access']}"}
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = tokens["account_id"]
        return self.http_get(USAGE_URL, headers=headers)

    def fetch(self) -> list[Meter]:
        tokens = self._store().load()
        if not tokens or not tokens.get("access"):
            raise ProviderError(NOT_SIGNED_IN)
        if float(tokens.get("expires_s", 0)) - EXPIRY_MARGIN_S <= time.time():
            tokens = self._refresh(tokens)
        try:
            data = self._get_usage(tokens)
        except HttpError as exc:
            if exc.status != 401:
                raise ProviderError(str(exc)) from exc
            try:  # Revoked early; renew once and retry.
                data = self._get_usage(self._refresh(tokens))
            except HttpError as retry_exc:
                raise ProviderError(f"ChatGPT rejected the sign-in ({retry_exc}). Use 'Sign in "
                                    "to Codex' in the tray menu.") from retry_exc
        meters = parse_usage(data)
        if not meters:
            raise ProviderError("The usage response contained no usage windows.")
        return meters
