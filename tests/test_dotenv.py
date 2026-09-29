"""Tests for the .env loader."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quota_monitor.dotenv import DotenvError, load_dotenv, parse_dotenv


class ParseDotenvTests(unittest.TestCase):
    def test_supported_syntax(self):
        text = (
            "# comment\n"
            "\n"
            "PLAIN=abc\n"
            'DOUBLE="has spaces"\n'
            "SINGLE='has # hash'\n"
            "export EXPORTED=1\n"
            "INLINE=value # trailing note\n"
            "EMPTY=\n"
            "  SPACED  =  padded  \n"
        )
        self.assertEqual(parse_dotenv(text), {
            "PLAIN": "abc", "DOUBLE": "has spaces", "SINGLE": "has # hash",
            "EXPORTED": "1", "INLINE": "value", "EMPTY": "", "SPACED": "padded",
        })

    def test_value_may_contain_equals_sign(self):
        self.assertEqual(parse_dotenv("TOKEN=abc=def==")["TOKEN"], "abc=def==")

    def test_malformed_line_reports_line_number(self):
        with self.assertRaisesRegex(DotenvError, "line 2"):
            parse_dotenv("OK=1\nnot a valid line\n")

    def test_invalid_key_is_rejected(self):
        with self.assertRaises(DotenvError):
            parse_dotenv("1BAD=x")


class LoadDotenvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(load_dotenv(self.path), [])

    def test_real_environment_takes_precedence(self):
        self.path.write_text("QM_A=from_file\nQM_B=from_file\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"QM_A": "from_env"}, clear=True):
            applied = load_dotenv(self.path)
            self.assertEqual(os.environ["QM_A"], "from_env")
            self.assertEqual(os.environ["QM_B"], "from_file")
        self.assertEqual(applied, ["QM_B"])

    def test_windows_byte_order_mark_is_ignored(self):
        self.path.write_text("QM_KEY=value\n", encoding="utf-8-sig")
        with mock.patch.dict(os.environ, {}, clear=True):
            load_dotenv(self.path)
            self.assertEqual(os.environ.get("QM_KEY"), "value")


if __name__ == "__main__":
    unittest.main()
