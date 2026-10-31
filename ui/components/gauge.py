"""Ring gauge and bar sparkline. Plain helper classes exposing `.control`.

`LineChartSpark` uses `flet_charts.LineChart` when the package is
importable, and transparently falls back to the bar `Sparkline` otherwise
(or when the user disables line charts in Settings).
"""
from __future__ import annotations

from typing import Optional, Sequence

import flet as ft

from services.charts import (build_series, charts_available, nice_y_max, pick_color,
                             pick_stroke_width)

LEVEL_COLORS = {"ok": ft.Colors.GREEN, "warn": ft.Colors.AMBER, "crit": ft.Colors.RED}


class RingGauge:
    def __init__(self, title: str, size: int = 92) -> None:
        self._ring = ft.ProgressRing(value=0, stroke_width=9, width=size, height=size)
        self._pct = ft.Text("—", size=18, weight=ft.FontWeight.W_700)
        self._sub = ft.Text("", size=11, opacity=0.7)
        self.control = ft.Column(
            [ft.Stack([self._ring,
                       ft.Container(width=size, height=size, alignment=ft.Alignment.CENTER,
                                    content=self._pct)], width=size, height=size),
             ft.Text(title, size=12, weight=ft.FontWeight.W_600), self._sub],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2)

    def set(self, pct: Optional[float], lvl: str = "ok", sub: str = "") -> None:
        self._ring.value = 0 if pct is None else max(0.0, min(1.0, pct / 100))
        self._ring.color = LEVEL_COLORS[lvl]
        self._pct.value = "—" if pct is None else f"{pct:.0f}%"
        self._sub.value = sub


class Sparkline:
    """Dependency-free mini chart: a row of bars scaled to the data."""

    def __init__(self, n: int = 60, height: int = 44) -> None:
        self.n, self.height = n, height
        self._bars = [ft.Container(expand=True, height=1, border_radius=1, opacity=0.25)
                      for _ in range(n)]
        self.control = ft.Row(self._bars, spacing=1, height=height,
                              vertical_alignment=ft.CrossAxisAlignment.END)

    def set(self, values: Sequence[float], vmax: Optional[float] = None,
            color: Optional[str] = None) -> None:
        vals = list(values)[-self.n:]
        top = vmax if vmax else (max(vals, default=0) * 1.2 or 1.0)
        pad = self.n - len(vals)
        for i, bar in enumerate(self._bars):
            if i < pad:
                bar.height, bar.opacity = 1, 0.15
            else:
                v = vals[i - pad]
                bar.height = max(1, min(self.height, v / top * self.height))
                bar.opacity = 1.0
                bar.bgcolor = color


class LineChartSpark:
    """real `flet_charts.LineChart` sparkline.

    Falls back to the bar `Sparkline` automatically when:
      - the `flet-charts` package isn't installed (`charts_available()` → False)
      - the caller passes `enabled=False` (e.g. user disabled line charts in Settings)

    Same `.control` / `.set()` shape as `Sparkline`, so the Monitor page can swap
    one for the other without other code changes.
    """

    def __init__(self, kind: str = "cpu", height: int = 80, n: int = 120,
                 enabled: bool = True) -> None:
        self.kind, self.n, self.height = kind, n, height
        self._fallback = Sparkline(n=min(60, n), height=height)
        self._enabled = enabled and charts_available()
        self._chart: Optional[ft.Control] = None
        self._series_pts: list[tuple[float, float]] = []
        self._fixed_max: Optional[float] = 100.0 if kind in ("cpu", "ram") else None
        self._color = pick_color(kind)
        self._stroke = pick_stroke_width(kind)
        if self._enabled:
            self._chart = self._make_chart()
            self.control = self._chart
        else:
            self.control = self._fallback.control

    def _make_chart(self) -> ft.Control:
        """Build an empty `flet_charts.LineChart`. Rebuilt (cheaply) on each `set()`."""
        try:
            import flet_charts as fc
        except ImportError:                                # pragma: no cover
            self._enabled = False
            return self._fallback.control
        # Use a fixed height instead of expand=True so the chart renders
        # properly inside cards/containers that don't have bounded height.
        chart = fc.LineChart(
            height=self.height,
            width=float("inf"),       # fill available width
            interactive=False,
            animation=False,
            horizontal_grid_lines=fc.ChartGridLines(),
            left_axis=fc.ChartAxis(show_labels=False, show_min=False, show_max=False),
            bottom_axis=fc.ChartAxis(show_labels=False, show_min=False, show_max=False),
            min_x=0, max_x=float(self.n - 1),
            min_y=0, max_y=self._fixed_max or 1.0,
        )
        return chart

    def set(self, values: Sequence[float], vmax: Optional[float] = None,
            color: Optional[str] = None) -> None:
        """Update the chart. `vmax` and `color` are accepted for API parity with
        `Sparkline`; we use them only on the fallback path."""
        if not self._enabled or self._chart is None:
            # Fall back to the bar sparkline. Pick a sensible color.
            self._fallback.set(values, vmax=vmax, color=color or self._color)
            return
        # Native LineChart path. Build the points list and replace the series.
        try:
            import flet_charts as fc
        except ImportError:                                # pragma: no cover
            self._enabled = False
            self._fallback.set(values, vmax=vmax, color=color or self._color)
            return
        vals = list(values)
        pts = build_series(vals, fixed_max=None,
                           padding=max(0, self.n - len(vals)))
        y_max = nice_y_max(vals, fixed_max=vmax or self._fixed_max)
        data_color = color or self._color
        # Rebuild the data_series list entirely (new list object) so Flet
        # detects the change and re-renders the chart. Mutating in place
        # sometimes doesn't trigger a redraw on Windows/desktop.
        new_series = [
            fc.LineChartData(
                points=[fc.LineChartDataPoint(x=x, y=y, show_tooltip=False) for x, y in pts],
                color=data_color,
                stroke_width=self._stroke,
                curved=True,
                point=False,
                selected_point=False,
                above_line_bgcolor=None,
                below_line_bgcolor=None,
            )
        ]
        # Assign a NEW list so Flet's diffing picks up the change.
        self._chart.data_series = new_series
        self._chart.max_y = y_max if y_max > 0 else 1.0
        self._chart.max_x = float(max(self.n - 1, len(pts) - 1))

