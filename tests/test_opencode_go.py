"""Tests for the OpenCode Go provider."""

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from helpers import FakeHttp
from quota_monitor.http import HttpError
from quota_monitor.providers.base import ProviderError
from quota_monitor.providers.opencode_go import (
    OpenCodeGoProvider, normalize_cookie, parse_page, workspace_id,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

# Server-rendered page data as embedded JavaScript (unquoted keys, !0/!1 booleans).
PAGE_JS = (
    '<html><script>_$HY.r["lite.subscription.get[\\"wrk_1\\"]"]=$R[0]={mine:!0,useBalance:!1,'
    'region:"us",rollingUsage:{status:"ok",resetInSec:5400,usagePercent:20,usage:1200,limit:6000},'
    'weeklyUsage:{status:"ok",resetInSec:302400,usagePercent:48,usage:1,limit:2},'
    'monthlyUsage:{status:"rate-limited",resetInSec:864000,usagePercent:100,usage:5,limit:5}}</script></html>'
)
PAGE_JSON = ('{"rollingUsage": {"status": "ok", "resetInSec": 60, "usagePercent": 7.5}, '
             '"weeklyUsage": {"usagePercent": 12, "resetInSec": 120}}')


class ParseTests(unittest.TestCase):
    def test_parses_embedded_javascript(self):
        meters = parse_page(PAGE_JS, now=NOW)
        self.assertEqual([(m.display_label, m.value) for m in meters],
                         [("5h", 20.0), ("Weekly", 48.0), ("Monthly", 100.0)])
        self.assertEqual(meters[0].resets_at, datetime(2026, 9, 30, 13, 30, tzinfo=timezone.utc))

    def test_parses_quoted_keys(self):
        meters = parse_page(PAGE_JSON, now=NOW)
        self.assertEqual([(m.display_label, m.value) for m in meters], [("5h", 7.5), ("Weekly", 12.0)])

    def test_no_data(self):
        self.assertEqual(parse_page("<html>Sign in</html>"), [])


class InputTests(unittest.TestCase):
    def test_workspace_from_id_or_url(self):
        self.assertEqual(workspace_id("wrk_ABC"), "wrk_ABC")
        self.assertEqual(workspace_id("https://opencode.ai/workspace/wrk_ABC/go?x=1"), "wrk_ABC")

    def test_cookie_forms(self):
        self.assertEqual(normalize_cookie("Cookie: auth=xyz"), "auth=xyz")
        self.assertEqual(normalize_cookie("xyz"), "auth=xyz")


class ProviderTests(unittest.TestCase):
    URL = "https://opencode.ai/workspace/wrk_1/go"

    def run_fetch(self, reply):
        http = FakeHttp({self.URL: reply})
        env = {"OC_COOKIE": "auth=secret", "OC_WS": "wrk_1"}
        with mock.patch.dict(os.environ, env):
            meters = OpenCodeGoProvider({"cookie_env": "OC_COOKIE", "workspace_env": "OC_WS"},
                                        http_get_text=http.get_text).fetch()
        return meters, http

    def test_fetches_go_page_with_cookie(self):
        meters, http = self.run_fetch(PAGE_JS)
        self.assertEqual(len(meters), 3)
        self.assertEqual(http.calls[0][2]["Cookie"], "auth=secret")

    def test_login_page_reports_expired_session(self):
        with self.assertRaisesRegex(ProviderError, "session may have expired"):
            self.run_fetch("<html>Sign in to OpenCode</html>")

    def test_http_403(self):
        with self.assertRaisesRegex(ProviderError, "fresh 'auth' cookie"):
            self.run_fetch(HttpError("HTTP 403", status=403))


if __name__ == "__main__":
    unittest.main()
