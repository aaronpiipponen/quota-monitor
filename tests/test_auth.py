"""Tests for the browser sign-in helper and token store, using a simulated browser."""

import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from quota_monitor.auth import LoginError, TokenStore, browser_login, pkce_pair


def fake_browser(extra=None, tamper_state=False):
    """Return an open_browser replacement that 'logs in' by calling the redirect URI."""
    def open_browser(url):
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        reply = {"code": "the-code", "state": "wrong" if tamper_state else params["state"]}
        reply.update(extra or {})
        target = f"{params['redirect_uri']}?{urlencode(reply)}"
        threading.Thread(target=lambda: urllib.request.urlopen(
            target.replace("localhost", "127.0.0.1"), timeout=5).read(), daemon=True).start()
    return open_browser


def build(redirect, challenge, state):
    return "https://login.example/authorize?" + urlencode(
        {"redirect_uri": redirect, "code_challenge": challenge, "state": state})


class BrowserLoginTests(unittest.TestCase):
    def test_receives_code_on_random_port(self):
        result = browser_login(build, port=0, path="/callback", open_browser=fake_browser(), timeout=5)
        self.assertEqual(result.code, "the-code")
        self.assertTrue(result.redirect_uri.startswith("http://localhost:"))
        self.assertTrue(result.redirect_uri.endswith("/callback"))

    def test_rejects_mismatched_state(self):
        with self.assertRaisesRegex(LoginError, "did not match"):
            browser_login(build, port=0, path="/callback", open_browser=fake_browser(tamper_state=True), timeout=5)

    def test_reports_refusal(self):
        with self.assertRaisesRegex(LoginError, "refused"):
            browser_login(build, port=0, path="/callback",
                          open_browser=fake_browser({"error": "access_denied"}), timeout=5)

    def test_times_out(self):
        with self.assertRaisesRegex(LoginError, "timed out"):
            browser_login(build, port=0, path="/callback", open_browser=lambda url: None, timeout=0.3)

    def test_pkce_pair_differs_each_time(self):
        self.assertNotEqual(pkce_pair(), pkce_pair())


class TokenStoreTests(unittest.TestCase):
    def test_round_trip_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TokenStore(Path(tmp), "x")
            self.assertIsNone(store.load())
            store.save({"refresh": "r"})
            self.assertEqual(TokenStore(Path(tmp), "x").load(), {"refresh": "r"})


if __name__ == "__main__":
    unittest.main()
