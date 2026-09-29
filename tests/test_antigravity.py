"""Tests for the Antigravity provider."""

import tempfile
import unittest
from pathlib import Path

from helpers import FakeHttp
from quota_monitor.auth import TokenStore, now_ms
from quota_monitor.http import HttpError
from quota_monitor.providers import antigravity
from quota_monitor.providers.antigravity import (
    LOAD_URL, MODELS_URL, SUMMARY_URL, TOKEN_URL, USERINFO_URL, AntigravityProvider, parse_models, parse_summary,
)
from quota_monitor.providers.base import ProviderError
from test_auth import fake_browser

SUMMARY = {"groups": [
    {"displayName": "Gemini", "buckets": [
        {"window": "weekly", "remainingFraction": 0.67, "resetTime": "2026-10-04T00:00:00Z"},
        {"window": "5h", "remainingFraction": 1, "resetTime": "2026-09-29T15:00:00Z"}]},
    {"displayName": "Claude/GPT", "buckets": [
        {"window": "5h"}, {"window": "weekly", "remainingFraction": 0.9}]},
]}


class ParseTests(unittest.TestCase):
    def test_summary(self):
        meters = parse_summary(SUMMARY)
        self.assertEqual([m.display_label for m in meters],
                         ["Gemini 5h", "Gemini weekly", "Claude/GPT 5h", "Claude/GPT weekly"])
        self.assertEqual([round(m.value) for m in meters], [0, 33, 100, 10])

    def test_models_fallback(self):
        data = {"models": {"gemini-3-pro": {"quotaInfo": {"remainingFraction": 0.2}},
                           "claude-sonnet": {"quotaInfo": {"remainingFraction": 0.5}}}}
        self.assertEqual({m.display_label: round(m.value) for m in parse_models(data)},
                         {"Claude/GPT quota": 50, "Gemini quota": 80})


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.store = TokenStore(self.state, "antigravity")

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, http):
        return AntigravityProvider({}, http_get=http.get, http_post=http.post,
                                   http_post_json=http.post_json, state_dir=self.state)

    def test_login_stores_refresh_email_and_project(self):
        http = FakeHttp({TOKEN_URL: {"access_token": "a", "refresh_token": "1//r", "expires_in": 3599},
                         USERINFO_URL: {"email": "me@x.com"},
                         LOAD_URL: {"cloudaicompanionProject": {"id": "proj-1"}}})
        original = antigravity.CALLBACK_PORT
        antigravity.CALLBACK_PORT = 0
        try:
            message = antigravity.login(self.state, http_post=http.post, http_get=http.get,
                                        http_post_json=http.post_json, open_browser=fake_browser())
        finally:
            antigravity.CALLBACK_PORT = original
        self.assertIn("me@x.com", message)
        saved = self.store.load()
        self.assertEqual((saved["refresh"], saved["project"]), ("1//r", "proj-1"))
        self.assertIn("client_secret", http.calls[0][3])

    def test_not_signed_in(self):
        with self.assertRaisesRegex(ProviderError, "Sign in to Antigravity"):
            self.provider(FakeHttp()).fetch()

    def test_uses_cached_access_then_renews_when_expired(self):
        self.store.save({"refresh": "1//r", "access": "a1", "expires_ms": now_ms() + 3_600_000, "project": "p"})
        http = FakeHttp({SUMMARY_URL: [SUMMARY, SUMMARY], TOKEN_URL: {"access_token": "a2", "expires_in": 3599}})
        self.provider(http).fetch()
        self.assertEqual(http.calls[0][3], {"project": "p"})
        tokens = self.store.load()
        tokens["expires_ms"] = 0
        self.store.save(tokens)
        self.provider(http).fetch()
        self.assertEqual([c[0] for c in http.calls], ["POST_JSON", "POST", "POST_JSON"])
        self.assertEqual(http.calls[-1][2]["Authorization"], "Bearer a2")

    def test_falls_back_to_models(self):
        self.store.save({"refresh": "1//r", "access": "a", "expires_ms": now_ms() + 3_600_000})
        http = FakeHttp({SUMMARY_URL: HttpError("HTTP 404", status=404),
                         MODELS_URL: {"models": {"gemini-3-pro": {"quotaInfo": {"remainingFraction": 0.4}}}}})
        [meter] = self.provider(http).fetch()
        self.assertEqual(meter.display_label, "Gemini quota")

    def test_forbidden_shows_googles_reason(self):
        self.store.save({"refresh": "1//r", "access": "a", "expires_ms": now_ms() + 3_600_000})
        error = HttpError("HTTP 403", status=403, body="This service has been disabled in this account")
        http = FakeHttp({SUMMARY_URL: error, MODELS_URL: error})
        with self.assertRaisesRegex(ProviderError, "disabled in this account"):
            self.provider(http).fetch()


if __name__ == "__main__":
    unittest.main()
