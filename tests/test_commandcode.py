"""Tests for the Command Code provider."""

import os
import unittest
from unittest import mock

from helpers import FakeHttp
from quota_monitor.http import HttpError
from quota_monitor.providers.base import ProviderError
from quota_monitor.providers.commandcode import (
    CREDITS_URL, SUBSCRIPTIONS_URL, CommandCodeProvider, normalize_cookie, parse_usage,
)

CREDITS = {
    "credits": {"monthlyCredits": 7.5, "purchasedCredits": 2, "monthlyCreditsGranted": 10},
    "windowLimits": {
        "fiveHour": {"used": 1, "cap": 4, "resetAt": 1790700000000},
        "weekly": {"used": "3", "cap": "10", "resetAt": "2026-10-03T00:00:00Z"},
    },
}
SUBSCRIPTION = {"success": True, "data": {"planId": "goat", "status": "active",
                                          "currentPeriodEnd": "2026-10-12T00:00:00Z"}}


class ParseTests(unittest.TestCase):
    def test_all_three_windows_and_top_up(self):
        meters = {m.display_label: m for m in parse_usage(CREDITS, SUBSCRIPTION)}
        self.assertEqual(list(meters), ["5h", "Weekly", "Monthly", "Top-up credits"])
        self.assertEqual(meters["5h"].value, 25.0)
        self.assertEqual(meters["Weekly"].value, 30.0)
        self.assertEqual(meters["Monthly"].value, 25.0)          # (10 - 7.5) / 10
        self.assertEqual(meters["Monthly"].resets_at.day, 12)
        self.assertEqual(int(meters["5h"].resets_at.timestamp()), 1790700000)

    def test_without_grant_shows_remaining_balance(self):
        data = {"credits": {"monthlyCredits": 3}}
        [meter] = parse_usage(data)
        self.assertEqual((meter.display_label, meter.value), ("Monthly credits left", 3.0))

    def test_rejects_missing_credits(self):
        with self.assertRaises(ProviderError):
            parse_usage({"error": "unauthorized"})


class CookieTests(unittest.TestCase):
    def test_accepts_header_line_and_bare_pairs(self):
        self.assertEqual(normalize_cookie("Cookie: a=1; b=2"), "a=1; b=2")
        self.assertEqual(normalize_cookie('"a=1"'), "a=1")

    def test_rejects_value_without_name(self):
        with self.assertRaises(ProviderError):
            normalize_cookie("just-a-token")


class ProviderTests(unittest.TestCase):
    def provider(self, http):
        return CommandCodeProvider({"cookie_env": "TEST_CC_COOKIE"}, http_get=http.get)

    def test_sends_cookie_and_tolerates_subscription_failure(self):
        http = FakeHttp({CREDITS_URL: CREDITS, SUBSCRIPTIONS_URL: HttpError("timeout")})
        with mock.patch.dict(os.environ, {"TEST_CC_COOKIE": "session=abc"}):
            meters = self.provider(http).fetch()
        self.assertEqual(http.calls[0][2]["Cookie"], "session=abc")
        monthly = [m for m in meters if m.display_label == "Monthly"][0]
        self.assertIsNone(monthly.resets_at)

    def test_expired_session_message(self):
        http = FakeHttp({CREDITS_URL: HttpError("HTTP 401", status=401)})
        with mock.patch.dict(os.environ, {"TEST_CC_COOKIE": "session=abc"}):
            with self.assertRaisesRegex(ProviderError, "fresh cookie"):
                self.provider(http).fetch()


if __name__ == "__main__":
    unittest.main()
