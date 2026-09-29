"""Antigravity provider: Google AI subscription limits (Gemini and Claude/GPT groups).

Sign-in: Google account login in the browser (tray menu → Sign in to Antigravity).
Usage: the Cloud Code endpoint ``v1internal:retrieveUserQuotaSummary`` (5h and weekly
buckets per group), with ``v1internal:fetchAvailableModels`` as a fallback. Both are
undocumented. Tokens are kept in data/auth/antigravity.json and renewed automatically.

Google's terms prohibit using Antigravity sign-ins from software other than Google's.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.parse import urlencode

from ..auth import LoginError, TokenStore, browser_login, now_ms
from ..http import HttpError, get_json, post_form, post_json
from ..models import KIND_PERCENT_USED, Meter
from .base import Provider, ProviderError, parse_timestamp

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
API_BASE = "https://cloudcode-pa.googleapis.com"
LOAD_URL = f"{API_BASE}/v1internal:loadCodeAssist"
SUMMARY_URL = f"{API_BASE}/v1internal:retrieveUserQuotaSummary"
MODELS_URL = f"{API_BASE}/v1internal:fetchAvailableModels"
CALLBACK_PORT = 51121  # The redirect port registered for this client.
SCOPES = " ".join([
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
])
# Antigravity desktop app OAuth client. Google treats installed-app client secrets as
# non-confidential. ANTIGRAVITY_CLIENT_ID / ANTIGRAVITY_CLIENT_SECRET in .env override them.
DEFAULT_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "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"
ANTIGRAVITY_VERSION = "1.18.3"
EXPIRY_MARGIN_MS = 120_000
NOT_SIGNED_IN = "Not signed in. Use 'Sign in to Antigravity' in the tray menu."


def client() -> tuple[str, str]:
    return (os.environ.get("ANTIGRAVITY_CLIENT_ID", "").strip() or DEFAULT_CLIENT_ID,
            os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "").strip() or DEFAULT_CLIENT_SECRET)


def _metadata() -> dict[str, str]:
    return {"ideType": "ANTIGRAVITY", "platform": "WINDOWS" if sys.platform == "win32" else "MACOS",
            "pluginType": "GEMINI"}


def request_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
                       f"Gecko) Antigravity/{ANTIGRAVITY_VERSION} Chrome/138.0.7204.235 "
                       f"Electron/37.3.1 Safari/537.36"),
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(_metadata(), separators=(",", ":")),
    }


def _project_from_load(payload: Any) -> str:
    project = payload.get("cloudaicompanionProject") if isinstance(payload, dict) else None
    if isinstance(project, dict):
        project = project.get("id")
    return project if isinstance(project, str) else ""


def login(state_dir, http_post=post_form, http_get=get_json, http_post_json=post_json,
          open_browser=None) -> str:
    client_id, secret = client()

    def build(redirect: str, challenge: str, state: str) -> str:
        return AUTHORIZE_URL + "?" + urlencode({
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect,
            "scope": SCOPES, "code_challenge": challenge, "code_challenge_method": "S256",
            "state": state, "access_type": "offline", "prompt": "consent"})

    kwargs = {"open_browser": open_browser} if open_browser else {}
    result = browser_login(build, port=CALLBACK_PORT, path="/oauth-callback", **kwargs)
    try:
        data = http_post(TOKEN_URL, {"grant_type": "authorization_code", "code": result.code,
                                     "redirect_uri": result.redirect_uri, "client_id": client_id,
                                     "client_secret": secret, "code_verifier": result.verifier})
    except HttpError as exc:
        raise LoginError(f"Google did not accept the sign-in: {exc}") from exc
    if not isinstance(data, dict) or not data.get("refresh_token"):
        raise LoginError("Google returned no refresh token for this sign-in.")
    access = data.get("access_token", "")

    email = project = ""
    try:  # Best effort: the email is only shown in messages.
        info = http_get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        email = str(info.get("email", "")) if isinstance(info, dict) else ""
    except HttpError:
        pass
    try:  # Best effort: quota requests also work without a project.
        project = _project_from_load(http_post_json(LOAD_URL, {"metadata": _metadata()},
                                                    headers=request_headers(access)))
    except HttpError:
        pass

    TokenStore(state_dir, "antigravity").save({
        "refresh": data["refresh_token"], "access": access,
        "expires_ms": now_ms() + int(data.get("expires_in") or 3600) * 1000,
        "email": email, "project": project})
    return f"Signed in to Antigravity{f' as {email}' if email else ''}."


def _used_percent(remaining_fraction: Any) -> float:
    # Protobuf JSON omits zero values, so a missing fraction means nothing remains.
    try:
        fraction = float(remaining_fraction)
    except (TypeError, ValueError):
        fraction = 0.0
    return (1.0 - max(0.0, min(1.0, fraction))) * 100


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def parse_summary(data: Any) -> list[Meter]:
    """Map quota groups and their 5h/weekly buckets to meters, 5h first."""
    meters: list[Meter] = []
    for group in (data.get("groups") if isinstance(data, dict) else None) or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("displayName") or "Quota").strip()
        buckets = [b for b in group.get("buckets") or [] if isinstance(b, dict)]

        def key_of(bucket: dict) -> str:
            return str(bucket.get("window") or bucket.get("bucketId") or "").lower()

        for bucket in sorted(buckets, key=lambda b: 0 if "5h" in key_of(b) else 1 if "week" in key_of(b) else 2):
            key = key_of(bucket)
            window = "5h" if "5h" in key else "weekly" if "week" in key else \
                str(bucket.get("displayName") or key or "limit")
            meters.append(Meter(provider="antigravity", name=f"{_slug(group_name)}_{_slug(window)}",
                                kind=KIND_PERCENT_USED, value=_used_percent(bucket.get("remainingFraction")),
                                unit="%", resets_at=parse_timestamp(bucket.get("resetTime")),
                                label=f"{group_name} {window}"))
    return meters


def parse_models(data: Any) -> list[Meter]:
    """Fallback: per-model quotas, reduced to the most-used model in each family."""
    families: dict[str, tuple[float, Any]] = {}
    for model_id, entry in ((data.get("models") if isinstance(data, dict) else None) or {}).items():
        info = entry.get("quotaInfo") if isinstance(entry, dict) else None
        if not isinstance(info, dict):
            continue
        name = str(model_id).lower()
        family = "Gemini" if "gemini" in name else "Claude/GPT" if ("claude" in name or "gpt" in name) else None
        if family is None:
            continue
        used = _used_percent(info.get("remainingFraction"))
        if family not in families or used > families[family][0]:
            families[family] = (used, info.get("resetTime"))
    return [Meter(provider="antigravity", name=f"{_slug(f)}_quota", kind=KIND_PERCENT_USED, value=used,
                  unit="%", resets_at=parse_timestamp(reset), label=f"{f} quota")
            for f, (used, reset) in sorted(families.items())]


class AntigravityProvider(Provider):
    name = "antigravity"

    def _access_token(self, tokens: dict[str, Any]) -> str:
        if tokens.get("access") and int(tokens.get("expires_ms", 0)) - EXPIRY_MARGIN_MS > now_ms():
            return tokens["access"]
        client_id, secret = client()
        try:
            data = self.http_post(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": tokens["refresh"],
                                              "client_id": client_id, "client_secret": secret})
        except HttpError as exc:
            if "invalid_grant" in exc.body:
                raise ProviderError("Google revoked the sign-in. Use 'Sign in to Antigravity' "
                                    "in the tray menu.") from exc
            raise ProviderError(f"Token renewal failed: {exc}") from exc
        if not isinstance(data, dict) or not data.get("access_token"):
            raise ProviderError("Token renewal returned an unexpected response.")
        tokens.update(access=data["access_token"],
                      expires_ms=now_ms() + int(data.get("expires_in") or 3600) * 1000)
        TokenStore(self.state_dir, "antigravity").save(tokens)
        return tokens["access"]

    def _post(self, url: str, access: str, body: dict) -> Any:
        try:
            return self.http_post_json(url, body, headers=request_headers(access))
        except HttpError as exc:
            if exc.status == 401:
                raise ProviderError("Google rejected the access token (HTTP 401).") from exc
            if exc.status == 403:
                detail = f" Google says: {exc.body.strip()[:160]}" if exc.body.strip() else ""
                raise ProviderError(f"Google refused access (HTTP 403).{detail}") from exc
            raise ProviderError(str(exc)) from exc

    def fetch(self) -> list[Meter]:
        tokens = TokenStore(self.state_dir, "antigravity").load()
        if not tokens or not tokens.get("refresh"):
            raise ProviderError(NOT_SIGNED_IN)
        access = self._access_token(tokens)
        body = {"project": tokens["project"]} if tokens.get("project") else {}
        summary_error = None
        try:
            meters = parse_summary(self._post(SUMMARY_URL, access, body))
            if meters:
                return meters
        except ProviderError as exc:
            summary_error = exc
        try:
            meters = parse_models(self._post(MODELS_URL, access, body))
        except ProviderError:
            raise summary_error or ProviderError("Quota request failed.")
        if not meters:
            raise summary_error or ProviderError("Google returned no quota information.")
        return meters
