"""Tests for SQLite persistence."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quota_monitor.models import KIND_PERCENT_USED, Meter, PollResult
from quota_monitor.storage import Storage

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def meter(value, name="weekly"):
    return Meter(provider="claude", name=name, kind=KIND_PERCENT_USED, value=value, unit="%",
                 resets_at=T0 + timedelta(days=1), label="Weekly")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "nested" / "quota.db")

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def test_creates_missing_parent_directories(self):
        self.assertTrue((Path(self.tmp.name) / "nested" / "quota.db").exists())

    def test_latest_status_returns_newest_success(self):
        self.storage.save(PollResult("claude", T0, meters=[meter(10)]))
        self.storage.save(PollResult("claude", T0 + timedelta(minutes=10), meters=[meter(25)]))
        [row] = self.storage.latest_status()
        self.assertTrue(row["ok"])
        self.assertEqual(row["meters"][0]["value"], 25)
        self.assertEqual(row["meters"][0]["label"], "Weekly")

    def test_failed_poll_keeps_last_good_values(self):
        self.storage.save(PollResult("claude", T0, meters=[meter(40)]))
        self.storage.save(PollResult("claude", T0 + timedelta(minutes=10), error="HTTP 500"))
        [row] = self.storage.latest_status()
        self.assertFalse(row["ok"])
        self.assertEqual(row["error"], "HTTP 500")
        self.assertEqual(row["last_ok_at"], T0.isoformat())
        self.assertEqual(row["meters"][0]["value"], 40)

    def test_provider_that_never_succeeded_has_no_meters(self):
        self.storage.save(PollResult("gemini", T0, error="no credential"))
        [row] = self.storage.latest_status()
        self.assertIsNone(row["last_ok_at"])
        self.assertEqual(row["meters"], [])


if __name__ == "__main__":
    unittest.main()
