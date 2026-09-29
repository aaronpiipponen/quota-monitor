"""Tests for the Claude provider."""

import tempfile
import unittest
from pathlib import Path

from helpers import FakeHttp
from quota_monitor.auth import TokenStore, now_ms
from quota_monitor.http import HttpError
from quota_monitor.providers import claude
from quota_monitor.providers.base import ProviderError
from quota_monitor.providers.claude import TOKEN_URL, USAGE_URL, ClaudeProvider, parse_usage
from test_auth import fake_browser

USAGE = {
    "five_hour": {"utilization": 20.0, "resets_at": "2026-09-29T14:00:00.000000+00:00"},
    "seven_day": {"utilization": 48.0, "resets_at": "2026-10-01T00:00:00Z"},
    "seven_day_sonnet": {"utilization": 5.0},
    "limits": [
        {"kind": "weekly_scoped", "group": "weekly", "percent": 11,
         "resets_at": "2026-10-01T00:00:00Z", "scope": {"model": {"display_name": "Fable"}}},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 48,
         "scope": {"model": {"display_name": "All models"}}},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 7,
         "scope": {"model": {"display_name": "Sonnet"}}},
    ],
}


class ParseUsageTests(unittest.TestCase):
    def test_maps_windows_in_display_order(self):
        meters = parse_usage(USAGE)
        self.assertEqual([m.display_label for m in meters], ["5h", "Weekly", "Fable weekly", "Sonnet weekly"])
        self.assertEqual(meters[0].resets_at.hour, 14)

    def test_scoped_limit_supersedes_flat_field(self):
        [sonnet] = [m for m in parse_usage(USAGE) if m.display_label == "Sonnet weekly"]
        self.assertEqual(sonnet.value, 7)

    def test_extra_usage_when_enabled(self):
        data = {"seven_day": {"utilization": 1},
                "extra_usage": {"is_enabled": True, "monthly_limit": 50, "used_credits": 10}}
        [extra] = [m for m in parse_usage(data) if m.name == "extra_usage"]
        self.assertAlmostEqual(extra.value, 20.0)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.store = TokenStore(self.state, "claude")

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, http):
        return ClaudeProvider({}, http_get=http.get, http_post=http.post, state_dir=self.state)

    def test_login_stores_tokens(self):
        http = FakeHttp({TOKEN_URL: {"access_token": "a", "refresh_token": "r", "expires_in": 3600}})
        claude.login(self.state, http_post_json=http.post_json, open_browser=fake_browser())
        self.assertEqual(http.calls[0][3]["code"], "the-code")
        self.assertEqual(self.store.load()["refresh"], "r")

    def test_not_signed_in(self):
        with self.assertRaisesRegex(ProviderError, "Sign in to Claude"):
            self.provider(FakeHttp()).fetch()

    def test_valid_token_used_directly(self):
        self.store.save({"access": "a", "refresh": "r", "expires_ms": now_ms() + 3_600_000})
        http = FakeHttp({USAGE_URL: USAGE})
        self.provider(http).fetch()
        self.assertEqual(http.calls[0][2]["Authorization"], "Bearer a")

    def test_expired_token_renewed_and_rotated_refresh_saved(self):
        self.store.save({"access": "old", "refresh": "r1", "expires_ms": now_ms() - 1})
        http = FakeHttp({TOKEN_URL: {"access_token": "new", "refresh_token": "r2", "expires_in": 3600},
                         USAGE_URL: USAGE})
        self.provider(http).fetch()
        self.assertEqual(http.calls[0][3]["refresh_token"], "r1")
        self.assertEqual(self.store.load()["refresh"], "r2")

    def test_401_renews_once_and_retries(self):
        self.store.save({"access": "stale", "refresh": "r", "expires_ms": now_ms() + 3_600_000})
        http = FakeHttp({USAGE_URL: [HttpError("HTTP 401", status=401), USAGE],
                         TOKEN_URL: {"access_token": "new", "expires_in": 3600}})
        self.assertTrue(self.provider(http).fetch())
        self.assertEqual(http.calls[-1][2]["Authorization"], "Bearer new")

    def test_invalid_grant_asks_to_sign_in(self):
        self.store.save({"access": "old", "refresh": "r", "expires_ms": now_ms() - 1})
        http = FakeHttp({TOKEN_URL: HttpError("HTTP 400", status=400, body='{"error":"invalid_grant"}')})
        with self.assertRaisesRegex(ProviderError, "Sign in to Claude"):
            self.provider(http).fetch()


if __name__ == "__main__":
    unittest.main()
