"""Build line-chart point lists from the Monitor history deques.

Pure logic — no Flet imports, so this is testable without a UI. The UI layer
(`ui/components/gauge.LineChartSpark`) consumes the output of `build_series` and
constructs `flet_charts.LineChartDataPoint` objects from it.

The history is a `dict[str, deque[float]]` with keys ("cpu", "ram", "down", "up"),
each up to 120 samples long. We expose helpers that:
  - probe whether `flet_charts` is importable (so the UI can fall back gracefully),
  - normalise the samples into `(x, y)` points with x = sample index,
  - compute a sane y-range (with an optional fixed ceiling like 100 for %),
  - pick a friendly color and stroke width.
"""
from __future__ import annotations

from collections import deque
from typing import Optional, Sequence

# ---- module-level import probe -------------------------------------------
#
# Done lazily so the rest of the app doesn't pay the import cost when charts
# aren't needed. The result is cached on the function object so we don't re-probe
# on every Monitor render.

def charts_available() -> bool:
    """True if `flet_charts.LineChart` can be imported on this system."""
    if hasattr(charts_available, "_cached"):
        return charts_available._cached  # type: ignore[attr-defined]
    try:
        import flet_charts  # noqa: F401
        from flet_charts import LineChart  # noqa: F401
        charts_available._cached = True  # type: ignore[attr-defined]
    except ImportError:
        charts_available._cached = False  # type: ignore[attr-defined]
    return charts_available._cached  # type: ignore[attr-defined]


# ---- series building ------------------------------------------------------

def build_series(values: Sequence[float],
                 fixed_max: Optional[float] = None,
                 padding: int = 0) -> list[tuple[float, float]]:
    """Convert a deque/list of samples into `[(x, y), ...]` points.

    - `fixed_max`: if given (e.g. 100 for CPU/RAM %), the chart's y-axis is
      pinned to [0, fixed_max]; otherwise the max is auto-scaled.
    - `padding`: optional count of leading `0`-points so a freshly-started
      history still fills the chart's x-axis (avoids the "growing from the left"
      look). Negative padding is ignored.
    """
    vals = list(values)
    if padding > 0:
        vals = [0.0] * padding + vals
    return [(float(i), float(v)) for i, v in enumerate(vals)]


def nice_y_max(values: Sequence[float], fixed_max: Optional[float] = None,
               headroom: float = 1.15) -> float:
    """Pick a friendly upper bound for the y-axis.

    - If `fixed_max` is given (CPU/RAM percent), use it directly.
    - Otherwise, take the max sample and add ~15% headroom, with a tiny floor
      so an all-zero history doesn't divide by zero.
    """
    if fixed_max is not None:
        return float(fixed_max)
    peak = max(values, default=0.0)
    if peak <= 0:
        return 1.0
    return float(peak * headroom)


def pick_color(kind: str, default: str = "#3B82F6") -> str:
    """Per-series color. `kind` is one of {"cpu", "ram", "down", "up"}."""
    return {
        "cpu": "#22C55E",    # green — matches the "ok" ring gauge color
        "ram": "#F59E0B",    # amber
        "down": "#3B82F6",   # blue
        "up": "#14B8A6",     # teal
    }.get(kind, default)


def pick_stroke_width(kind: str) -> float:
    """Network series get a slightly thicker line because the values are smaller."""
    return 2.5 if kind in ("down", "up") else 2.0


def last_value_str(values: Sequence[float], fmt: str = "{:.0f}%") -> str:
    """Format the most recent sample for the card's title row. Returns '—' if empty."""
    if not values:
        return "—"
    return fmt.format(float(values[-1]))


def should_use_line_charts(setting: bool) -> bool:
    """Convenience for the UI: respect both the user setting AND availability.

    Called from `MonitorView` so it doesn't have to remember to probe both.
    """
    return bool(setting) and charts_available()
