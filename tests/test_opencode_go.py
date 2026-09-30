"""Tests for the OpenCode Go provider."""

import os
import unittest
from unittest import mock

from helpers import FakeHttp
from quota_monitor.http import HttpError
from quota_monitor.providers.base import ProviderError
from quota_monitor.providers.opencode_go import STATUS_URL, OpenCodeGoProvider, normalize_cookie, org_id, parse_status

# Shape of a real response (identifiers shortened).
STATUS = {
    "product": "go", "useBalance": False,
    "access": {
        "startsAt": "2026-09-30T08:00:23.000Z", "endsAt": "2026-10-30T08:00:23.000Z",
        "meters": {
            "fiveHour": {"startsAt": None, "resetsAt": None, "limitMicroCents": "1200000000", "usedMicroCents": "0"},
            "week": {"startsAt": "2026-09-28T00:00:00.000Z", "resetsAt": "2026-10-05T00:00:00.000Z",
                     "limitMicroCents": "3000000000", "usedMicroCents": "1440000000"},
            "month": {"resetsAt": "2026-10-30T08:00:23.000Z", "limitMicroCents": "6000000000",
                      "usedMicroCents": "6000000000"},
        },
    },
}


class ParseTests(unittest.TestCase):
    def test_meters_and_resets(self):
        meters = parse_status(STATUS)
        self.assertEqual([(m.display_label, m.value) for m in meters],
                         [("5h", 0.0), ("Weekly", 48.0), ("Monthly", 100.0)])
        self.assertIsNone(meters[0].resets_at)  # 5h window not started yet.
        self.assertEqual(meters[1].resets_at.isoformat(), "2026-10-05T00:00:00+00:00")

    def test_no_subscription(self):
        with self.assertRaisesRegex(ProviderError, "No active OpenCode Go"):
            parse_status({"product": None})

    def test_cookie_forms(self):
        self.assertEqual(normalize_cookie("auth=abc"), "auth=abc")
        self.assertEqual(normalize_cookie("abc"), "auth=abc")
        self.assertEqual(normalize_cookie("Cookie: auth=abc; other=1"), "auth=abc; other=1")

    def test_org_from_id_or_url(self):
        self.assertEqual(org_id("org_01ABC"), "org_01ABC")
        self.assertEqual(org_id("https://opencode.ai/console/org_01ABC/go"), "org_01ABC")
        with self.assertRaises(ProviderError):
            org_id("https://opencode.ai/console")


class ProviderTests(unittest.TestCase):
    def run_fetch(self, reply):
        http = FakeHttp({STATUS_URL: reply})
        env = {"OC_COOKIE": "auth=secret", "OC_ORG": "https://opencode.ai/console/org_01ABC"}
        with mock.patch.dict(os.environ, env):
            meters = OpenCodeGoProvider({"cookie_env": "OC_COOKIE", "org_env": "OC_ORG"},
                                        http_get=http.get).fetch()
        return meters, http

    def test_sends_cookie(self):
        meters, http = self.run_fetch(STATUS)
        self.assertEqual(len(meters), 3)
        self.assertEqual(http.calls[0][2]["Cookie"], "auth=secret")
        self.assertEqual(http.calls[0][2]["X-Org-Id"], "org_01ABC")

    def test_other_errors_include_server_message(self):
        with self.assertRaisesRegex(ProviderError, "OpenCode says: missing org"):
            self.run_fetch(HttpError("HTTP 400", status=400, body="missing org"))

    def test_expired_session_variants(self):
        for error in (HttpError("HTTP 401", status=401),
                      HttpError(f"Response from {STATUS_URL} was not valid JSON")):
            with self.subTest(error=str(error)), self.assertRaisesRegex(ProviderError, "fresh Cookie value"):
                self.run_fetch(error)


if __name__ == "__main__":
    unittest.main()
