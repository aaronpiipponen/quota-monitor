"""The popup panel: a frameless, rounded window listing providers and their meters."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import (
    QEasingCurve, QElapsedTimer, QEvent, QParallelAnimationGroup, QPoint, QPropertyAnimation,
    QRect, QRectF, Qt, Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..models import KIND_PERCENT_USED, utc_now
from . import theme
from .format import format_ago, format_meter_value, format_reset, parse_iso, severity

PANEL_WIDTH = 360
CORNER_RADIUS = 14
# Gap between the panel and the screen edges (taskbar side and right side), in pixels.
EDGE_GAP = 8
# Open/close animation: the panel slides this far while fading in or out.
SLIDE_DISTANCE = 20
SHOW_DURATION_MS = 180
HIDE_DURATION_MS = 120


def _label(text: str, color: str, point_size: float = 9.5, bold: bool = False) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSizeF(point_size)
    font.setBold(bold)
    label.setFont(font)
    label.setStyleSheet(f"color: {color}; background: transparent;")
    return label


class MeterBar(QWidget):
    """A rounded progress bar: a faint track in the accent colour and a solid fill."""

    def __init__(self, fraction: float, color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.fraction = max(0.0, min(fraction, 1.0))
        self.color = QColor(color)
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        radius = rect.height() / 2

        track = QColor(self.color)
        track.setAlpha(50)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        if self.fraction > 0:
            # Never draw narrower than the bar height, or the rounded ends distort.
            width = max(rect.height(), rect.width() * self.fraction)
            painter.setBrush(self.color)
            painter.drawRoundedRect(QRectF(0, 0, width, rect.height()), radius, radius)


def _build_meter(meter: dict[str, Any], accent: str, now) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(3)

    is_percent = meter["kind"] == KIND_PERCENT_USED
    value_color = theme.DANGER if is_percent and severity(meter["value"]) == "danger" else accent

    top = QHBoxLayout()
    top.addWidget(_label(meter.get("label") or meter["meter"], theme.TEXT, 10))
    top.addStretch(1)
    top.addWidget(_label(format_meter_value(meter), value_color, 10, bold=True))
    layout.addLayout(top)

    if is_percent:
        layout.addWidget(MeterBar(meter["value"] / 100.0, accent))
    elif meter.get("limit_value"):
        layout.addWidget(MeterBar(meter["value"] / meter["limit_value"], accent))

    reset_text = format_reset(parse_iso(meter.get("resets_at")), now)
    if reset_text:
        layout.addWidget(_label(reset_text, theme.MUTED, 9))
    return box


def _build_section(row: dict[str, Any], now) -> QWidget:
    name, accent = theme.provider_style(row["provider"])
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(_label(f"●  {name}", accent, 11.5, bold=True))

    if not row["ok"]:
        stale = ""
        if row.get("last_ok_at"):
            stale = f" Showing data from {format_ago(parse_iso(row['last_ok_at']), now)}."
        error = _label(f"Update failed: {row['error']}.{stale}", theme.WARN, 9)
        error.setWordWrap(True)
        layout.addWidget(error)

    if not row.get("meters"):
        placeholder = "Waiting for first poll…" if row["ok"] else "No data available."
        layout.addWidget(_label(placeholder, theme.MUTED, 9))
    for meter in row.get("meters", []):
        layout.addWidget(_build_meter(meter, accent, now))
    return box


class QuotaPanel(QWidget):
    refresh_requested = Signal()
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        # Tool: no taskbar entry. Frameless: we draw our own rounded background.
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("AI Quota")
        self.setFixedWidth(PANEL_WIDTH)
        self.auto_hide = True
        self._hidden_clock = QElapsedTimer()
        self._animation: Optional[QParallelAnimationGroup] = None
        self._hiding = False
        # +1 slides downward on hide (taskbar at the bottom), -1 slides upward (taskbar at the top).
        self._slide_direction = 1
        self._rows: list[dict[str, Any]] = []
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(_label("AI Quota", theme.TEXT, 13, bold=True))
        header.addStretch(1)
        self._refresh_button = self._header_button("↻", "Refresh now", self.refresh_requested.emit)
        header.addWidget(self._refresh_button)
        header.addWidget(self._header_button("✕", "Close", self.close_requested.emit))
        root.addLayout(header)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._sections = QVBoxLayout(self._content)
        self._sections.setContentsMargins(0, 0, 4, 0)
        self._sections.setSpacing(18)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {theme.BUTTON_HOVER}; border-radius: 3px;"
            " min-height: 24px; }"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page,"
            " QScrollBar::sub-page { background: none; height: 0; }"
        )
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

        self._footer = _label("", theme.MUTED, 8.5)
        root.addWidget(self._footer)

    def _header_button(self, glyph: str, tooltip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(glyph)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"QToolButton {{ color: {theme.TEXT}; background: transparent; border: none;"
            f" border-radius: 6px; font-size: 15px; padding: 2px 7px; }}"
            f"QToolButton:hover {{ background: {theme.BUTTON_HOVER}; }}"
            f"QToolButton:disabled {{ color: {theme.MUTED}; }}"
        )
        button.clicked.connect(slot)
        return button

    # ----- data ------------------------------------------------------------

    def set_status(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.render()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_button.setEnabled(not busy)
        self._update_footer()

    def render(self) -> None:
        """Rebuild all sections. Cheap enough to call every 30 s for live countdowns."""
        now = utc_now()
        while self._sections.count():
            widget = self._sections.takeAt(0).widget()
            if widget is not None:
                # Detach immediately: deleteLater() alone leaves the old widget visible
                # until the event loop next runs, which shows up as overlapping text.
                widget.setParent(None)
                widget.deleteLater()
        widgets = [_build_section(row, now) for row in self._rows] or [
            _label("No providers enabled.", theme.MUTED, 10)]
        for widget in widgets:
            self._sections.addWidget(widget)
            # Widgets added to an already-visible parent are only shown on the next
            # event-loop pass, and layouts ignore hidden widgets when measuring.
            # Showing them now makes _fit_height() see the real content size.
            widget.show()
        self._sections.addStretch(1)
        self._update_footer()
        self._fit_height()

    def _update_footer(self) -> None:
        if self._busy:
            self._footer.setText("Refreshing…")
            return
        times = [parse_iso(r["last_polled_at"]) for r in self._rows if r.get("last_polled_at")]
        self._footer.setText(f"Updated {format_ago(max(times), utc_now())}" if times else "")

    def _fit_height(self) -> None:
        """Size the scroll area to its content, capped at 80 % of the screen height."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        max_height = int(screen.availableGeometry().height() * 0.8) - 110
        self._content.adjustSize()
        self._scroll.setFixedHeight(max(60, min(self._content.sizeHint().height(), max_height)))
        self.adjustSize()

    # ----- showing / hiding --------------------------------------------------

    def is_open(self) -> bool:
        """True when the panel is shown and not in the middle of closing."""
        return self.isVisible() and not self._hiding

    def show_near(self, anchor: Optional[QRect]) -> None:
        """Slide the panel in at the right edge of the screen, next to the taskbar.

        The tray icon's geometry is used only to pick the screen and to detect a
        taskbar at the top. It is not used for horizontal placement, because Linux
        tray implementations often report an empty geometry and centring on the icon
        leaves the panel awkwardly offset from the edge on Windows.
        """
        if self.is_open():
            self.raise_()
            self.activateWindow()
            return

        valid = anchor is not None and anchor.isValid() and not anchor.isEmpty()
        screen = (QGuiApplication.screenAt(anchor.center()) if valid else None) \
            or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.render()  # Render first so the final height is known before positioning.

        at_top = valid and anchor.center().y() < area.center().y()
        self._slide_direction = -1 if at_top else 1
        # QRect.right()/bottom() are inclusive (left + width - 1), so compute from x/width.
        x = area.x() + area.width() - self.width() - EDGE_GAP
        y = area.y() + EDGE_GAP if at_top else area.y() + area.height() - self.height() - EDGE_GAP
        target = QPoint(x, y)
        start = target + QPoint(0, SLIDE_DISTANCE * self._slide_direction)

        self._stop_animation()
        self._hiding = False
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._run_animation(start, target, 0.0, 1.0, SHOW_DURATION_MS, QEasingCurve.Type.OutCubic)

    def hide_animated(self) -> None:
        """Slide and fade the panel out, then hide it."""
        if not self.is_open():
            return
        self._hiding = True
        self._hidden_clock.start()
        start = self.pos()
        end = start + QPoint(0, SLIDE_DISTANCE * self._slide_direction)
        self._stop_animation()
        animation = self._run_animation(start, end, self.windowOpacity(), 0.0,
                                        HIDE_DURATION_MS, QEasingCurve.Type.InCubic)
        animation.finished.connect(self._finish_hide)

    def _finish_hide(self) -> None:
        self.hide()
        self._hiding = False
        self.setWindowOpacity(1.0)  # Reset so a plain show() elsewhere is never invisible.

    def _run_animation(self, start: QPoint, end: QPoint, opacity_from: float, opacity_to: float,
                       duration: int, easing: QEasingCurve.Type) -> QParallelAnimationGroup:
        """Animate position and opacity together.

        Window opacity needs a compositor. Where none is available (some Linux setups)
        Qt ignores it and only the slide is visible, which is an acceptable fallback.
        """
        group = QParallelAnimationGroup(self)
        for prop, begin, finish in ((b"pos", start, end), (b"windowOpacity", opacity_from, opacity_to)):
            animation = QPropertyAnimation(self, prop, group)
            animation.setStartValue(begin)
            animation.setEndValue(finish)
            animation.setDuration(duration)
            animation.setEasingCurve(easing)
            group.addAnimation(animation)
        self._animation = group
        # Deliberately not DeleteWhenStopped: we keep a reference in self._animation, and
        # auto-deletion would leave that reference pointing at a freed C++ object.
        group.start()
        return group

    def _stop_animation(self) -> None:
        # stop() does not emit finished(), so an interrupted hide never calls _finish_hide().
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None

    def recently_hidden(self, within_ms: int = 300) -> bool:
        """True right after an auto-hide started. Clicking the tray icon while the panel is
        open first deactivates it (starting the hide), then delivers the click; this lets
        the click act as 'close' instead of immediately reopening the panel."""
        return self._hidden_clock.isValid() and self._hidden_clock.elapsed() < within_ms

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.WindowDeactivate and self.auto_hide:
            self.hide_animated()
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BACKGROUND))
        painter.drawRoundedRect(QRectF(self.rect()), CORNER_RADIUS, CORNER_RADIUS)
