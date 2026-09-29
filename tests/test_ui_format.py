"""Tests for the Qt-free UI formatting helpers."""

import unittest
from datetime import datetime, timedelta, timezone

from quota_monitor.models import KIND_BALANCE, KIND_PERCENT_USED
from quota_monitor.ui.format import (
    format_ago, format_duration, format_meter_value, format_reset, highest_usage, parse_iso, severity,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


class FormatDurationTests(unittest.TestCase):
    def test_matches_reference_design(self):
        cases = {
            timedelta(hours=1, minutes=24): "1 hour, 24 minutes",
            timedelta(days=1, hours=10): "1 day, 10 hours",
            timedelta(days=5): "5 days",
            timedelta(minutes=6): "6 minutes",
            timedelta(days=6, hours=23, minutes=59): "6 days, 23 hours",
            timedelta(days=2, minutes=5): "2 days",
            timedelta(seconds=30): "under a minute",
        }
        for delta, expected in cases.items():
            with self.subTest(delta=delta):
                self.assertEqual(format_duration(delta.total_seconds()), expected)


class OtherFormattingTests(unittest.TestCase):
    def test_reset_text(self):
        self.assertEqual(format_reset(NOW + timedelta(days=5), NOW), "Resets in 5 days")
        self.assertEqual(format_reset(NOW - timedelta(minutes=1), NOW), "Reset due now")
        self.assertIsNone(format_reset(None, NOW))

    def test_ago_text(self):
        self.assertEqual(format_ago(NOW - timedelta(seconds=10), NOW), "just now")
        self.assertEqual(format_ago(NOW - timedelta(minutes=25), NOW), "25 minutes ago")
        self.assertEqual(format_ago(None, NOW), "never")

    def test_meter_values(self):
        self.assertEqual(format_meter_value({"kind": KIND_PERCENT_USED, "value": 47.6, "unit": "%"}), "48% used")
        self.assertEqual(format_meter_value({"kind": KIND_BALANCE, "value": 1234.5, "unit": "USD"}), "1,234.50 USD")

    def test_severity_thresholds(self):
        self.assertEqual([severity(v) for v in (None, 69.9, 70, 89.9, 90, 100)],
                         ["ok", "ok", "warn", "warn", "danger", "danger"])

    def test_parse_iso_assumes_utc_for_naive(self):
        self.assertEqual(parse_iso("2026-09-29T12:00:00"), NOW)
        self.assertIsNone(parse_iso(None))

    def test_highest_usage_ignores_balances(self):
        rows = [
            {"provider": "deepseek", "meters": [{"meter": "b", "label": "Balance", "kind": KIND_BALANCE, "value": 500}]},
            {"provider": "claude", "meters": [{"meter": "w", "label": "Weekly", "kind": KIND_PERCENT_USED, "value": 48}]},
        ]
        self.assertEqual(highest_usage(rows), (48, "claude", "Weekly"))


if __name__ == "__main__":
    unittest.main()
