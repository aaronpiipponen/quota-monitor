"""Smoke tests for the panel, rendered offscreen. Skipped if PySide6 is missing."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class PanelSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from quota_monitor.ui.demo import demo_status
        from quota_monitor.ui.panel import QuotaPanel

        self.panel = QuotaPanel()
        self.panel.set_status(demo_status())
        self.area = QGuiApplication.primaryScreen().availableGeometry()

    def tearDown(self):
        self.panel.hide()
        self.panel.deleteLater()

    def _open_and_settle(self, anchor=None):
        self.panel.show_near(anchor)
        QTest.qWait(400)  # Longer than the show animation.

    def test_panel_renders_demo_data(self):
        self._open_and_settle()
        self.assertGreater(self.panel.height(), 200)
        self.assertFalse(self.panel.grab().isNull())

    def test_panel_is_anchored_to_bottom_right_edge(self):
        from quota_monitor.ui.panel import EDGE_GAP

        self._open_and_settle()
        right_gap = self.area.x() + self.area.width() - (self.panel.x() + self.panel.width())
        bottom_gap = self.area.y() + self.area.height() - (self.panel.y() + self.panel.height())
        self.assertEqual((right_gap, bottom_gap), (EDGE_GAP, EDGE_GAP))

    def test_tray_icon_position_does_not_shift_panel_left(self):
        # Simulates Windows, where the tray icon sits left of the clock.
        icon = QRect(self.area.x() + self.area.width() - 300, self.area.y() + self.area.height() - 20, 24, 24)
        self._open_and_settle(icon)
        self.assertEqual(self.panel.x() + self.panel.width(),
                         self.area.x() + self.area.width() - 8)

    def test_top_taskbar_places_panel_at_top(self):
        from quota_monitor.ui.panel import EDGE_GAP

        self._open_and_settle(QRect(self.area.x() + self.area.width() - 40, self.area.y() + 2, 24, 24))
        self.assertEqual(self.panel.y(), self.area.y() + EDGE_GAP)

    def test_hide_animated_hides_and_resets_opacity(self):
        self._open_and_settle()
        self.panel.hide_animated()
        self.assertFalse(self.panel.is_open())   # Closing counts as not open immediately.
        self.assertTrue(self.panel.recently_hidden())
        QTest.qWait(300)
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.panel.windowOpacity(), 1.0)

    def test_reopening_during_hide_leaves_panel_open(self):
        self._open_and_settle()
        self.panel.hide_animated()
        QTest.qWait(40)                          # Interrupt the hide animation midway.
        self._open_and_settle()
        QTest.qWait(300)                         # Past the point where the hide would have ended.
        self.assertTrue(self.panel.is_open())
        self.assertTrue(self.panel.isVisible())


if __name__ == "__main__":
    unittest.main()
