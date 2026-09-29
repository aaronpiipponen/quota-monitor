"""Tests for autostart file content and command formatting."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quota_monitor import autostart


class AutostartTests(unittest.TestCase):
    def test_desktop_entry_quotes_paths_with_spaces(self):
        entry = autostart.desktop_entry(Path("/home/a b/.venv/bin/python"), Path("/home/a b/run_tray.pyw"))
        self.assertIn('Exec="/home/a b/.venv/bin/python" "/home/a b/run_tray.pyw"', entry)

    def test_windows_command(self):
        command = autostart.windows_command(Path("C:/p/pythonw.exe"), Path("C:/p/run_tray.pyw"))
        self.assertEqual(command, '"C:/p/pythonw.exe" "C:/p/run_tray.pyw"')

    @unittest.skipIf(sys.platform == "win32", "Linux desktop-file behaviour")
    def test_enable_disable_round_trip_on_linux(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            self.assertFalse(autostart.is_enabled())
            autostart.enable()
            self.assertTrue(autostart.is_enabled())
            autostart.disable()
            self.assertFalse(autostart.is_enabled())
            autostart.disable()  # Disabling twice is harmless.


if __name__ == "__main__":
    unittest.main()
