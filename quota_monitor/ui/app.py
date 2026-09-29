"""Tray controller: owns the tray icon, the panel, the poll timer and background polling."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QLockFile, QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import autostart
from ..auth import LoginError
from ..config import AppConfig
from ..poller import poll_all
from ..providers import LOGINS, ProviderError
from ..storage import Storage
from . import theme
from .demo import demo_status
from .format import highest_usage, severity
from .panel import QuotaPanel

COUNTDOWN_REFRESH_MS = 30_000
# At login the app can start before the panel's tray area exists. Wait this long
# for it before falling back to showing the panel as an ordinary window.
TRAY_WAIT_ATTEMPTS = 15
TRAY_WAIT_INTERVAL_MS = 2_000


def make_tray_icon(percent: Optional[float]) -> QIcon:
    """Draw a small ring gauge showing the highest usage across all providers."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(8, 8, size - 16, size - 16)

    painter.setPen(QPen(QColor("#6B7078"), 9))
    painter.drawEllipse(rect)
    if percent is not None and percent > 0:
        painter.setPen(QPen(QColor(theme.severity_color(severity(percent))), 9,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # Qt angles are in 1/16 degree; start at 12 o'clock and sweep clockwise.
        painter.drawArc(rect, 90 * 16, int(-min(percent, 100) / 100 * 360 * 16))
    painter.end()
    return QIcon(pixmap)


class _PollSignals(QObject):
    # 'object' lets us pass plain Python lists/dicts across threads.
    finished = Signal(object, object)  # (rows or None, error message or None)


class _PollTask(QRunnable):
    """Runs one poll cycle on a worker thread so the UI never freezes during slow requests.

    SQLite connections must not be shared between threads, so the worker opens its own.
    """

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.signals = _PollSignals()

    def run(self) -> None:
        try:
            storage = Storage(self.config.db_path)
            try:
                for result in poll_all(self.config):
                    storage.save(result)
                rows = storage.latest_status()
            finally:
                storage.close()
            self.signals.finished.emit(rows, None)
        except Exception:  # Report instead of silently killing the worker thread.
            self.signals.finished.emit(None, traceback.format_exc(limit=3))


class _LoginTask(QRunnable):
    """Runs a browser sign-in on a worker thread; it waits for the user in the browser."""

    def __init__(self, login, state_dir: Path) -> None:
        super().__init__()
        self.login = login
        self.state_dir = state_dir
        self.signals = _PollSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.login(self.state_dir), None)
        except (LoginError, ProviderError) as exc:
            self.signals.finished.emit(None, str(exc))
        except Exception as exc:  # Report instead of silently killing the worker thread.
            self.signals.finished.emit(None, f"Unexpected {type(exc).__name__}: {exc}")


class TrayController(QObject):
    def __init__(self, app: QApplication, config: Optional[AppConfig], demo: bool) -> None:
        super().__init__()
        self.app = app
        self.config = config
        self.demo = demo
        self._task: Optional[_PollTask] = None  # Keeps the running task (and its signals) alive.

        self.panel = QuotaPanel()
        self.panel.refresh_requested.connect(self.refresh)
        self.panel.close_requested.connect(self._on_close_requested)

        self.has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(make_tray_icon(None))
        self.tray.setToolTip("AI Quota")
        self._login_task: Optional[_LoginTask] = None
        self._menu = self._build_menu()  # QSystemTrayIcon does not take ownership of the menu.
        menu = self._menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)

        # Redraw countdowns periodically while the panel is visible.
        self._tick = QTimer(self)
        self._tick.timeout.connect(lambda: self.panel.isVisible() and self.panel.render())
        self._tick.start(COUNTDOWN_REFRESH_MS)

        if not demo:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self.refresh)
            self._poll_timer.start(config.poll_interval_minutes * 60_000)

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        def add(text: str, slot) -> QAction:
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
            return action

        add("Open", self.show_panel)
        add("Refresh now", self.refresh)
        if not self.demo:
            logins = [(key, LOGINS[str(settings.get("type", key))])
                      for key, settings in self.config.providers.items()
                      if settings.get("enabled", False) and str(settings.get("type", key)) in LOGINS]
            if logins:
                menu.addSeparator()
                for _key, (label, login) in logins:
                    add(label, lambda _checked=False, fn=login: self._start_login(fn))
            menu.addSeparator()
            self._autostart_action = add("Start at login", self._toggle_autostart)
            self._autostart_action.setCheckable(True)
            self._autostart_action.setChecked(autostart.is_enabled())
        menu.addSeparator()
        add("Quit", self.app.quit)
        return menu

    def _notify(self, message: str, error: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        if self.has_tray:
            self.tray.showMessage("AI Quota", message, icon, 8000)
        else:
            print(message, file=sys.stderr)

    def _start_login(self, login) -> None:
        if self._login_task is not None:
            self._notify("A sign-in is already in progress in your browser.")
            return
        self._login_task = _LoginTask(login, self.config.db_path.parent)
        self._login_task.signals.finished.connect(self._on_login_finished)
        QThreadPool.globalInstance().start(self._login_task)
        self._notify("Complete the sign-in in your browser.")

    def _on_login_finished(self, message: Optional[str], error: Optional[str]) -> None:
        self._login_task = None
        self._notify(error or message or "Signed in.", error=error is not None)
        if error is None:
            self.refresh()

    def _toggle_autostart(self, checked: bool) -> None:
        try:
            message = autostart.enable() if checked else autostart.disable()
        except autostart.AutostartError as exc:
            self._autostart_action.setChecked(not checked)
            self._notify(str(exc), error=True)
            return
        self._notify(message)

    def start(self) -> None:
        self.refresh()
        self._wait_for_tray(TRAY_WAIT_ATTEMPTS)

    def _wait_for_tray(self, attempts_left: int) -> None:
        self.has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        if self.has_tray:
            self.tray.show()
        elif attempts_left > 0:
            QTimer.singleShot(TRAY_WAIT_INTERVAL_MS, lambda: self._wait_for_tray(attempts_left - 1))
        else:
            # Without a tray there is no way to reopen a hidden panel, so keep it open
            # and make its close button quit the application.
            print("No system tray detected; showing the panel as a regular window.", file=sys.stderr)
            self.panel.auto_hide = False
            self.show_panel()

    # ----- polling -----------------------------------------------------------

    def refresh(self) -> None:
        if self.demo:
            self._apply(demo_status())
            return
        if self._task is not None:
            return  # A poll is already running.
        self.panel.set_busy(True)
        self._task = _PollTask(self.config)
        self._task.signals.finished.connect(self._on_polled)
        QThreadPool.globalInstance().start(self._task)

    def _on_polled(self, rows: Optional[list], error: Optional[str]) -> None:
        self._task = None
        self.panel.set_busy(False)
        if error is not None:
            print(f"Poll cycle failed:\n{error}", file=sys.stderr)
            self.tray.setToolTip("AI Quota: last refresh failed")
            return
        self._apply(self._order_rows(rows))

    def _order_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep only enabled providers, in config order, with placeholders for unpolled ones."""
        by_name = {row["provider"]: row for row in rows}
        ordered = []
        for name, settings in self.config.providers.items():
            if settings.get("enabled", False):
                ordered.append(by_name.get(name) or {
                    "provider": name, "last_polled_at": None, "ok": True, "error": None,
                    "last_ok_at": None, "meters": []})
        return ordered

    def _apply(self, rows: list[dict[str, Any]]) -> None:
        self.panel.set_status(rows)
        top = highest_usage(rows)
        self.tray.setIcon(make_tray_icon(top[0] if top else None))
        if top:
            name, _ = theme.provider_style(top[1])
            self.tray.setToolTip(f"AI Quota: highest usage {round(top[0])}% ({name} {top[2]})")
        else:
            self.tray.setToolTip("AI Quota")

    # ----- panel visibility ----------------------------------------------------

    def show_panel(self) -> None:
        self.panel.show_near(self.tray.geometry() if self.has_tray else None)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return  # Right-click opens the context menu; handled by Qt.
        if self.panel.is_open():
            self.panel.hide_animated()
        elif not self.panel.recently_hidden():
            self.show_panel()

    def _on_close_requested(self) -> None:
        if self.has_tray:
            self.panel.hide_animated()
        else:
            self.app.quit()


def run_tray(config: Optional[AppConfig], demo: bool) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Quota Monitor")
    # Essential for tray apps: hiding or closing the panel must not end the process.
    app.setQuitOnLastWindowClosed(False)

    lock = None
    if not demo:
        # Prevent two instances from polling concurrently and showing two tray icons.
        lock_path = Path(config.db_path).parent / "tray.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(lock_path))
        if not lock.tryLock(100):
            print("Quota Monitor is already running.", file=sys.stderr)
            return 1

    controller = TrayController(app, config, demo)
    controller.start()
    try:
        return app.exec()
    finally:
        if lock is not None:
            lock.unlock()
