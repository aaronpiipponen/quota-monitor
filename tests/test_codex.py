"""Tests for the Codex provider."""

import tempfile
import time
import unittest
from pathlib import Path

from helpers import FakeHttp, make_jwt
from quota_monitor.auth import TokenStore
from quota_monitor.http import HttpError
from quota_monitor.providers import codex
from quota_monitor.providers.base import ProviderError
from quota_monitor.providers.codex import TOKEN_URL, USAGE_URL, CodexProvider, parse_usage
from test_auth import fake_browser

USAGE = {
    "rate_limit": {
        "primary_window": {"used_percent": 54, "limit_window_seconds": 18000, "reset_at": 1790700000},
        "secondary_window": {"used_percent": 12, "limit_window_seconds": 604800, "reset_at": 1791000000},
    },
    "additional_rate_limits": [{"limit_name": "GPT-5.3-Codex-Spark", "rate_limit": {
        "primary_window": {"used_percent": 3, "limit_window_seconds": 18000, "reset_at": 1790700000}}}],
    "credits": {"has_credits": True, "unlimited": False, "balance": "12.5"},
}


def token_response(exp_offset=3600, refresh="r"):
    access = make_jwt({"exp": int(time.time()) + exp_offset})
    id_token = make_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}})
    return {"access_token": access, "refresh_token": refresh, "id_token": id_token}


class ParseUsageTests(unittest.TestCase):
    def test_maps_windows_extras_and_credits(self):
        meters = parse_usage(USAGE)
        self.assertEqual([m.display_label for m in meters], ["5h", "Weekly", "GPT-5.3-Codex-Spark 5h", "Credits"])
        self.assertEqual(int(meters[0].resets_at.timestamp()), 1790700000)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, http):
        return CodexProvider({}, http_get=http.get, http_post=http.post, state_dir=self.state)

    def test_login_then_fetch_sends_account_id(self):
        # The fixed callback port is used by the real flow; the test uses the same helper.
        http = FakeHttp({TOKEN_URL: token_response(), USAGE_URL: USAGE})
        original = codex.CALLBACK_PORT
        codex.CALLBACK_PORT = 0
        try:
            codex.login(self.state, http_post=http.post, open_browser=fake_browser())
        finally:
            codex.CALLBACK_PORT = original
        self.provider(http).fetch()
        headers = http.calls[-1][2]
        self.assertEqual(headers["ChatGPT-Account-Id"], "acct-1")

    def test_not_signed_in(self):
        with self.assertRaisesRegex(ProviderError, "Sign in to Codex"):
            self.provider(FakeHttp()).fetch()

    def test_expired_token_is_renewed(self):
        TokenStore(self.state, "codex").save({"access": "old", "refresh": "r1", "expires_s": time.time() - 10,
                                              "account_id": "acct-1"})
        http = FakeHttp({TOKEN_URL: token_response(refresh="r2"), USAGE_URL: USAGE})
        self.provider(http).fetch()
        self.assertEqual(http.calls[0][3]["refresh_token"], "r1")
        self.assertEqual(TokenStore(self.state, "codex").load()["refresh"], "r2")

    def test_rejected_refresh_asks_to_sign_in(self):
        TokenStore(self.state, "codex").save({"access": "old", "refresh": "r", "expires_s": 0})
        http = FakeHttp({TOKEN_URL: HttpError("HTTP 400", status=400)})
        with self.assertRaisesRegex(ProviderError, "Sign in to Codex"):
            self.provider(http).fetch()


if __name__ == "__main__":
    unittest.main()
