"""Tests for the DeepSeek provider using injected fake HTTP responses."""

import os
import unittest
from unittest import mock

from quota_monitor.http import HttpError
from quota_monitor.models import KIND_BALANCE
from quota_monitor.providers.base import ProviderError
from quota_monitor.providers.deepseek import BALANCE_URL, DeepSeekProvider, parse_balance

# Taken from the official API documentation example.
DOC_EXAMPLE_STRINGS = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00",
         "granted_balance": "10.00", "topped_up_balance": "100.00"}
    ],
}


def fake_http(response=None, error=None):
    """Build a stand-in for get_json that records calls and returns or raises."""
    calls = []

    def _get(url, headers=None, timeout=15.0):
        calls.append({"url": url, "headers": headers or {}})
        if error is not None:
            raise error
        return response

    _get.calls = calls
    return _get


class ParseBalanceTests(unittest.TestCase):
    def test_parses_string_amounts(self):
        meters = {m.name: m for m in parse_balance(DOC_EXAMPLE_STRINGS)}
        self.assertEqual(meters["balance_cny"].value, 110.0)
        self.assertEqual(meters["granted_cny"].value, 10.0)
        self.assertEqual(meters["topped_up_cny"].value, 100.0)
        self.assertTrue(all(m.kind == KIND_BALANCE and m.unit == "CNY" for m in meters.values()))

    def test_parses_numeric_amounts(self):
        data = {"is_available": True, "balance_infos": [
            {"currency": "USD", "total_balance": 5, "granted_balance": 0, "topped_up_balance": 5}]}
        meters = {m.name: m for m in parse_balance(data)}
        self.assertEqual(meters["balance_usd"].value, 5.0)
        self.assertEqual(meters["balance_usd"].display_label, "Balance")

    def test_multiple_currencies_produce_separate_meters(self):
        data = {"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "1"}, {"currency": "USD", "total_balance": "2"}]}
        names = sorted(m.name for m in parse_balance(data))
        self.assertEqual(names, ["balance_cny", "balance_usd"])

    def test_empty_balance_list_is_not_an_error(self):
        self.assertEqual(parse_balance({"is_available": False, "balance_infos": []}), [])

    def test_unexpected_shape_raises(self):
        with self.assertRaises(ProviderError):
            parse_balance({"error": "something"})

    def test_non_numeric_amount_raises(self):
        data = {"balance_infos": [{"currency": "CNY", "total_balance": "abc"}]}
        with self.assertRaises(ProviderError):
            parse_balance(data)


class DeepSeekProviderTests(unittest.TestCase):
    def test_sends_bearer_token_from_environment(self):
        http = fake_http(response=DOC_EXAMPLE_STRINGS)
        provider = DeepSeekProvider({"api_key_env": "TEST_DS_KEY"}, http_get=http)
        with mock.patch.dict(os.environ, {"TEST_DS_KEY": "sk-test"}):
            provider.fetch()
        self.assertEqual(http.calls[0]["url"], BALANCE_URL)
        self.assertEqual(http.calls[0]["headers"]["Authorization"], "Bearer sk-test")

    def test_environment_takes_precedence_over_literal_key(self):
        http = fake_http(response=DOC_EXAMPLE_STRINGS)
        provider = DeepSeekProvider({"api_key_env": "TEST_DS_KEY", "api_key": "sk-file"}, http_get=http)
        with mock.patch.dict(os.environ, {"TEST_DS_KEY": "sk-env"}):
            provider.fetch()
        self.assertEqual(http.calls[0]["headers"]["Authorization"], "Bearer sk-env")

    def test_missing_key_raises_without_calling_network(self):
        http = fake_http(response=DOC_EXAMPLE_STRINGS)
        provider = DeepSeekProvider({"api_key_env": "DEFINITELY_UNSET_VAR"}, http_get=http)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError):
                provider.fetch()
        self.assertEqual(http.calls, [])

    def test_http_401_is_reported_as_rejected_key(self):
        http = fake_http(error=HttpError("HTTP 401", status=401))
        provider = DeepSeekProvider({"api_key": "sk-bad"}, http_get=http)
        with self.assertRaisesRegex(ProviderError, "rejected"):
            provider.fetch()

    def test_network_error_is_wrapped(self):
        http = fake_http(error=HttpError("Network error contacting x: refused"))
        provider = DeepSeekProvider({"api_key": "sk"}, http_get=http)
        with self.assertRaisesRegex(ProviderError, "Network error"):
            provider.fetch()


if __name__ == "__main__":
    unittest.main()
