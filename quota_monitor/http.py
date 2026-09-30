"""Minimal JSON-over-HTTP helpers built on the standard library.

Using urllib instead of a third-party client keeps the backend dependency-free,
which simplifies installation on both Windows and Linux.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

USER_AGENT = "quota-monitor/1.0"
_BODY_EXCERPT = 300


class HttpError(Exception):
    """Raised for any transport, HTTP status or decoding failure.

    ``status`` holds the HTTP status code when the server responded, and is None
    for network-level failures (DNS, refused connection, timeout). ``body`` holds
    a short excerpt of the error response, which vendors use for error codes such
    as ``invalid_grant``. Messages never include request headers, so credentials
    cannot leak into logs or the database.
    """

    def __init__(self, message: str, status: Optional[int] = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _read(request: urllib.request.Request, url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    # HTTPError is a subclass of URLError, so it must be handled first.
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")[:_BODY_EXCERPT]
        except Exception:  # The body is best-effort diagnostic information only.
            body = ""
        raise HttpError(f"HTTP {exc.code} from {url}", status=exc.code, body=body) from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"Network error contacting {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpError(f"Timed out after {timeout:.0f}s contacting {url}") from exc


def _send(request: urllib.request.Request, url: str, timeout: float) -> Any:
    payload = _read(request, url, timeout)
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HttpError(f"Response from {url} was not valid JSON") from exc


def get_json(url: str, headers: Optional[dict[str, str]] = None, timeout: float = 20.0) -> Any:
    """Perform a GET request and return the decoded JSON body."""
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    return _send(urllib.request.Request(url, headers=request_headers, method="GET"), url, timeout)


def post_form(url: str, fields: dict[str, str], headers: Optional[dict[str, str]] = None,
              timeout: float = 20.0) -> Any:
    """POST form-encoded fields and return the decoded JSON body."""
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
    }
    request_headers.update(headers or {})
    data = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    return _send(request, url, timeout)


def post_json(url: str, payload: Any, headers: Optional[dict[str, str]] = None,
              timeout: float = 20.0) -> Any:
    """POST a JSON body and return the decoded JSON response."""
    request_headers = {"Accept": "application/json", "Content-Type": "application/json",
                       "User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    return _send(request, url, timeout)

