"""Base class and shared helpers for provider implementations."""

from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ..http import get_json, post_form, post_json
from ..models import Meter

HttpGet = Callable[..., Any]
HttpPost = Callable[..., Any]


class ProviderError(Exception):
    """A provider-level failure with a message suitable for display to the user.

    Messages are shown under the provider's heading, so they should not repeat
    the provider name, and should say what the user can do about the problem.
    """


class Provider(ABC):
    """Common interface for all vendors.

    HTTP functions are injected so tests can supply canned responses without
    touching the network. ``state_dir`` is a private folder (next to the database)
    where a provider may keep small files, such as a refreshed token cache.
    """

    name: str = ""

    def __init__(self, settings: dict[str, Any], http_get: HttpGet = get_json,
                 http_post: HttpPost = post_form, state_dir: Optional[Path] = None,
                 http_post_json: HttpPost = post_json) -> None:
        self.settings = settings
        self.http_get = http_get
        self.http_post = http_post
        self.http_post_json = http_post_json
        self.state_dir = state_dir

    @abstractmethod
    def fetch(self) -> list[Meter]:
        """Query the vendor and return the current meters. Raise ProviderError on failure."""

    def require_secret(self, key: str) -> str:
        """Resolve a secret, preferring an environment variable over the config file.

        For a key named ``api_key`` the lookup order is:
        1. The environment variable named by ``api_key_env`` (usually set via .env).
        2. The literal ``api_key`` value in the config file.
        """
        env_name = self.settings.get(f"{key}_env")
        if env_name:
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        value = str(self.settings.get(key, "")).strip()
        if value:
            return value
        hint = f"'{env_name}' in .env" if env_name else f"'{key}' in the config"
        raise ProviderError(f"No credential found (set {hint}).")

    def setting_path(self, key: str) -> Optional[Path]:
        """Return a user-configured path setting with '~' expanded, or None."""
        value = str(self.settings.get(key, "")).strip()
        return Path(value).expanduser() if value else None


def to_float(value: Any, context: str) -> float:
    """Convert numeric strings or numbers to float, raising ProviderError otherwise."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"{context}: expected a number, got {value!r}") from exc


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse the timestamp formats vendors use into an aware UTC datetime.

    Accepts ISO-8601 strings, Unix seconds and Unix milliseconds (values above
    1e11 are treated as milliseconds). Returns None for missing or unparsable
    values, because a missing reset time should never fail a whole poll.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_timestamp(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT's payload WITHOUT verifying it. Returns {} for opaque tokens.

    Only used to read hints such as the expiry time or account id. The token
    itself is still validated by the vendor when it is used.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def read_json_file(path: Path) -> Any:
    """Read a JSON file, converting common failures into ProviderError."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ProviderError(f"File not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"Could not read {path}: {exc}") from exc


def home() -> Path:
    return Path.home()
