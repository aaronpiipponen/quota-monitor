"""Shared test utilities."""

import base64
import json


def make_jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped token carrying the given claims."""
    def encode(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


class FakeHttp:
    """Records requests and replays canned responses (or raises canned errors) per URL."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def _reply(self, url):
        reply = self.responses[url]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get(self, url, headers=None, timeout=20.0):
        self.calls.append(("GET", url, headers or {}, None))
        return self._reply(url)

    def post(self, url, fields, headers=None, timeout=20.0):
        self.calls.append(("POST", url, headers or {}, fields))
        return self._reply(url)

    def post_json(self, url, payload, headers=None, timeout=20.0):
        self.calls.append(("POST_JSON", url, headers or {}, payload))
        return self._reply(url)

    def get_text(self, url, headers=None, timeout=20.0):
        self.calls.append(("GET_TEXT", url, headers or {}, None))
        return self._reply(url)
