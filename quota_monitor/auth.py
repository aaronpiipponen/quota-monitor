"""Browser sign-in (OAuth 2.0 with PKCE) and on-disk token storage.

Sign-in flow: a small HTTP server listens on localhost, the browser opens the
provider's login page, and after login the provider redirects the browser back
to that local server with an authorization code. The code is then exchanged for
tokens by the provider module.

Tokens are stored in data/auth/<provider>.json (owner-only permissions on Linux)
and include a refresh token, so signing in is only needed once per service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

LOGIN_TIMEOUT_S = 300

_DONE_PAGE = (b"<html><body style='font-family:sans-serif;text-align:center;margin-top:15%'>"
              b"<h2>Signed in</h2><p>You can close this tab.</p></body></html>")


class LoginError(Exception):
    """Sign-in failed or was cancelled; the message is shown to the user."""


@dataclass
class LoginResult:
    code: str
    state: str
    verifier: str
    redirect_uri: str


def pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def browser_login(build_url: Callable[[str, str, str], str], port: int, path: str,
                  open_browser: Callable[[str], Any] = webbrowser.open,
                  timeout: float = LOGIN_TIMEOUT_S) -> LoginResult:
    """Run one browser sign-in and return the authorization code.

    ``build_url(redirect_uri, code_challenge, state)`` returns the login page URL.
    ``port`` 0 picks a free port. Blocks until the redirect arrives or the timeout.
    """
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    received: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            url = urlparse(self.path)
            if url.path != path:
                self.send_response(404)
                self.end_headers()
                return
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            received.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE)
            done.set()

        def log_message(self, *args: Any) -> None:
            pass  # Keep request logs out of the application log.

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise LoginError(f"Could not listen on port {port} for the sign-in callback "
                         f"(is another sign-in in progress?): {exc}") from exc
    redirect_uri = f"http://localhost:{server.server_address[1]}{path}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    try:
        open_browser(build_url(redirect_uri, challenge, state))
        if not done.wait(timeout):
            raise LoginError("Sign-in timed out.")
    finally:
        server.shutdown()
        server.server_close()

    if "error" in received:
        raise LoginError(f"Sign-in was refused: {received.get('error_description') or received['error']}")
    code = received.get("code", "")
    # Some providers append '#<state>' to the code.
    code, _, code_state = code.partition("#")
    if not code:
        raise LoginError("The sign-in response contained no authorization code.")
    if received.get("state", code_state) != state:
        raise LoginError("The sign-in response did not match this sign-in attempt.")
    return LoginResult(code=code, state=state, verifier=verifier, redirect_uri=redirect_uri)


class TokenStore:
    """Small JSON file holding one provider's tokens."""

    def __init__(self, state_dir: Optional[Path], name: str) -> None:
        self.path = state_dir / "auth" / f"{name}.json" if state_dir else None

    def load(self) -> Optional[dict[str, Any]]:
        if self.path is None or not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict[str, Any]) -> None:
        if self.path is None:
            raise LoginError("No data folder is configured for storing sign-ins.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)  # Atomic: never leaves a half-written file.


def now_ms() -> int:
    return int(time.time() * 1000)
