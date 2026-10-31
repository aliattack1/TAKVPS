"""One set of human-readable formatters (sizes, durations, relative time)."""
from __future__ import annotations

import time


def human_size(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def human_duration(sec: float) -> str:
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m" if m else "<1m"


def time_ago(ts: float, now: float | None = None) -> str:
    if not ts:
        return "never"
    diff = (now if now is not None else time.time()) - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)} min ago"
    if diff < 86400:
        return f"{int(diff // 3600)} h ago"
    return f"{int(diff // 86400)} d ago"


def fmt_mtime(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"
