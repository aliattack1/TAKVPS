"""Phase 14 Part 1 tests: chart helpers + LineChartSpark fallback behaviour.

Pure-logic only — we test `services/charts.py` (no Flet imports) and the
fallback path of `LineChartSpark` (which exercises the bar sparkline).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import deque

from services import charts as charts_svc


# ======================================================================
# Phase 14 Part 1 — services/charts.py
# ======================================================================
class TestChartsAvailable(unittest.TestCase):
    def test_returns_bool_and_caches(self):
        # First call probes; both must return the same bool.
        a = charts_svc.charts_available()
        b = charts_svc.charts_available()
        self.assertIsInstance(a, bool)
        self.assertEqual(a, b)
        # The cache attribute must be set on the function object.
        self.assertTrue(hasattr(charts_svc.charts_available, "_cached"))


class TestBuildSeries(unittest.TestCase):
    def test_simple_series(self):
        pts = charts_svc.build_series([10, 20, 30])
        self.assertEqual(pts, [(0.0, 10.0), (1.0, 20.0), (2.0, 30.0)])

    def test_empty_series(self):
        self.assertEqual(charts_svc.build_series([]), [])

    def test_padding_prepends_zeros(self):
        # Padding 3 → three leading (i, 0) points then the real ones.
        pts = charts_svc.build_series([5, 10], padding=3)
        self.assertEqual(pts, [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 5.0), (4.0, 10.0)])

    def test_padding_zero_is_noop(self):
        pts = charts_svc.build_series([5, 10], padding=0)
        self.assertEqual(pts, [(0.0, 5.0), (1.0, 10.0)])

    def test_negative_padding_ignored(self):
        # `padding > 0` guard means negatives are skipped — matches `max(0, ...)` callers.
        pts = charts_svc.build_series([5, 10], padding=-2)
        self.assertEqual(pts, [(0.0, 5.0), (1.0, 10.0)])

    def test_accepts_deque(self):
        # The MonitorState.history stores deques; build_series must accept them.
        d = deque(maxlen=3)
        d.append(1.0); d.append(2.0); d.append(3.0)
        pts = charts_svc.build_series(d)
        self.assertEqual(len(pts), 3)
        self.assertEqual(pts[-1], (2.0, 3.0))

    def test_floats_preserved(self):
        pts = charts_svc.build_series([1.5, 2.5])
        self.assertEqual(pts[0], (0.0, 1.5))


class TestNiceYMax(unittest.TestCase):
    def test_fixed_max_wins(self):
        self.assertEqual(charts_svc.nice_y_max([50, 75], fixed_max=100), 100.0)

    def test_auto_scales_with_headroom(self):
        m = charts_svc.nice_y_max([10, 50, 80])
        self.assertAlmostEqual(m, 80 * 1.15, places=4)

    def test_empty_returns_floor(self):
        self.assertEqual(charts_svc.nice_y_max([]), 1.0)

    def test_all_zero_returns_floor(self):
        self.assertEqual(charts_svc.nice_y_max([0, 0, 0]), 1.0)

    def test_negative_values_use_peak(self):
        # Negative samples shouldn't break the calculation; we take the max.
        m = charts_svc.nice_y_max([-5, -2, -10])
        self.assertGreater(m, 0)


class TestPickColor(unittest.TestCase):
    def test_known_kinds_get_unique_colors(self):
        colors = {k: charts_svc.pick_color(k) for k in ("cpu", "ram", "down", "up")}
        # All four should be distinct (so the four sparklines don't look identical).
        self.assertEqual(len(set(colors.values())), 4)
        # And they should all be valid hex strings.
        for c in colors.values():
            self.assertTrue(c.startswith("#"))
            self.assertEqual(len(c), 7)

    def test_unknown_kind_falls_back_to_default(self):
        c = charts_svc.pick_color("???")
        self.assertEqual(c, "#3B82F6")


class TestPickStrokeWidth(unittest.TestCase):
    def test_network_series_thicker(self):
        # down/up are smaller numbers; we draw them thicker for visibility.
        self.assertGreaterEqual(charts_svc.pick_stroke_width("down"),
                                charts_svc.pick_stroke_width("cpu"))
        self.assertGreaterEqual(charts_svc.pick_stroke_width("up"),
                                charts_svc.pick_stroke_width("ram"))

    def test_unknown_kind_default(self):
        # The function returns 2.0 for anything it doesn't recognise.
        self.assertEqual(charts_svc.pick_stroke_width("???"), 2.0)


class TestLastValueStr(unittest.TestCase):
    def test_empty_returns_dash(self):
        self.assertEqual(charts_svc.last_value_str([]), "—")

    def test_formats_last(self):
        self.assertEqual(charts_svc.last_value_str([10, 20, 30]), "30%")

    def test_custom_format(self):
        # Bytes/sec formatting
        self.assertEqual(charts_svc.last_value_str([1000, 2500], fmt="{:.0f} B/s"), "2500 B/s")


class TestShouldUseLineCharts(unittest.TestCase):
    def test_setting_false_disables(self):
        # Even if charts are available, the user setting wins.
        self.assertFalse(charts_svc.should_use_line_charts(False))

    def test_setting_true_respects_availability(self):
        # When the user wants line charts but the package isn't installed,
        # we return False — that's the whole point of the gate.
        result = charts_svc.should_use_line_charts(True)
        avail = charts_svc.charts_available()
        self.assertEqual(result, avail)


# ======================================================================
# Phase 14 Part 1 — LineChartSpark fallback behaviour
# ======================================================================
class TestLineChartSparkFallback(unittest.TestCase):
    """Exercises the bar-sparkline fallback path (no flet_charts needed)."""

    def test_disabled_uses_bar_sparkline(self):
        from ui.components.gauge import LineChartSpark, Sparkline
        sp = LineChartSpark(kind="cpu", enabled=False)
        self.assertFalse(sp._enabled)
        # When disabled, .control is the fallback Sparkline's Row, not a LineChart.
        self.assertEqual(type(sp.control).__name__, "Row")

    def test_set_with_no_data_does_not_crash(self):
        from ui.components.gauge import LineChartSpark
        sp = LineChartSpark(kind="cpu", enabled=False)
        # Empty history: should be a no-op, not an exception.
        sp.set([])
        sp.set([0.0, 0.0, 0.0])

    def test_set_with_negative_values(self):
        from ui.components.gauge import LineChartSpark
        sp = LineChartSpark(kind="down", enabled=False)
        sp.set([-5.0, -2.0, 10.0])

    def test_kind_affects_color_choice(self):
        from ui.components.gauge import LineChartSpark
        # The stored color is what the chart uses when no override is passed.
        cpu = LineChartSpark(kind="cpu", enabled=False)
        down = LineChartSpark(kind="down", enabled=False)
        self.assertNotEqual(cpu._color, down._color)


# ======================================================================
# Phase 14 Part 1 — settings field + monitor wiring
# ======================================================================
class TestPhase14Settings(unittest.TestCase):
    def test_monitor_line_charts_default_on(self):
        from storage.settings import Settings
        s = Settings()
        self.assertTrue(hasattr(s, "monitor_line_charts"))
        self.assertTrue(s.monitor_line_charts, "line charts should default ON")


if __name__ == "__main__":
    unittest.main()
