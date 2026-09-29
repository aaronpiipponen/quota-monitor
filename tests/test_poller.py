"""Tests for failure isolation in the poll loop."""

import unittest
from pathlib import Path

from quota_monitor.config import AppConfig
from quota_monitor.models import KIND_BALANCE, Meter
from quota_monitor.poller import poll_all
from quota_monitor.providers.base import Provider, ProviderError


class GoodProvider(Provider):
    name = "good"

    def fetch(self):
        return [Meter("good", "balance", KIND_BALANCE, 1.0, "USD")]


class ExpectedFailure(Provider):
    name = "expected"

    def fetch(self):
        raise ProviderError("expected: vendor said no")


class BuggyProvider(Provider):
    name = "buggy"

    def fetch(self):
        raise KeyError("oops")


REGISTRY = {"good": GoodProvider, "expected": ExpectedFailure, "buggy": BuggyProvider}


def config(providers):
    return AppConfig(db_path=Path("unused.db"), poll_interval_minutes=10, providers=providers)


class PollerTests(unittest.TestCase):
    def test_failures_do_not_stop_other_providers(self):
        cfg = config({"buggy": {"enabled": True}, "expected": {"enabled": True},
                      "good": {"enabled": True}})
        results = {r.provider: r for r in poll_all(cfg, registry=REGISTRY)}
        self.assertTrue(results["good"].ok)
        self.assertIn("vendor said no", results["expected"].error)
        self.assertIn("KeyError", results["buggy"].error)

    def test_disabled_providers_are_skipped(self):
        cfg = config({"good": {"enabled": False}, "expected": {}})
        self.assertEqual(poll_all(cfg, registry=REGISTRY), [])

    def test_unknown_provider_is_reported(self):
        cfg = config({"nonexistent": {"enabled": True}})
        [result] = poll_all(cfg, registry=REGISTRY)
        self.assertIn("Unknown provider type", result.error)


if __name__ == "__main__":
    unittest.main()
