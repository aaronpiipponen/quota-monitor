"""Start the tray application at login.

Windows: a per-user value under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
Linux:   ~/.config/autostart/quota-monitor.desktop (XDG autostart).
Both start run_tray.pyw with the Python interpreter the application is running under.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "QuotaMonitor"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "run_tray.pyw"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartError(Exception):
    """Autostart could not be changed; the message is shown to the user."""


def desktop_file_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(config_home) if config_home else Path.home() / ".config") / "autostart" / "quota-monitor.desktop"


def desktop_entry(python: Path, launcher: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Quota Monitor\n"
        "Comment=AI subscription and API quota monitor\n"
        f'Exec="{python}" "{launcher}"\n'
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=5\n"
    )


def windows_command(pythonw: Path, launcher: Path) -> str:
    return f'"{pythonw}" "{launcher}"'


def _python() -> Path:
    python = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = python.with_name("pythonw.exe")
        if not pythonw.is_file():
            raise AutostartError(f"pythonw.exe not found next to {python}.")
        return pythonw
    return python


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_ID)
        except FileNotFoundError:
            return False
        return True
    return desktop_file_path().exists()


def enable() -> str:
    python = _python()
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, windows_command(python, LAUNCHER))
        else:
            path = desktop_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(desktop_entry(python, LAUNCHER), encoding="utf-8")
    except OSError as exc:
        raise AutostartError(f"Could not enable start at login: {exc}") from exc
    return "Quota Monitor will start at login."


def disable() -> str:
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_ID)
        else:
            desktop_file_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise AutostartError(f"Could not disable start at login: {exc}") from exc
    return "Quota Monitor will no longer start at login."
