"""Part 2 — persistent fleet-run history (JSON Lines).

Each finished fleet run is appended as one line per server, so the Activity
page and a future dedicated Fleet-History view can both read it. Mirrors the
shape of `storage/activity.py` (append-only, auto-trimmed at 3000 lines).

Pure: only stdlib imports; the file IO lives in this module so the runner
itself stays free of disk concerns.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

MAX_LINES = 2000
KEEP = 1500


def append_run(path: Path, result, now: Optional[float] = None) -> None:
    """Append one line per server in `result.runs`.

    `result` is a `services.fleet_runner.FleetRunResult`. We don't import the
    type here so this module stays free of the services package's import graph.
    """
    ts = now if now is not None else time.time()
    rows = []
    for r in result.runs:
        rows.append({
            "ts": ts,
            "fleet_started_at": result.started_at,
            "fleet_finished_at": result.finished_at,
            "command": result.command[:2000],
            "conn": r.profile_id,
            "name": r.profile_name,
            "target": r.target,
            "state": r.state.value if hasattr(r.state, "value") else str(r.state),
            "exit": r.exit_code,
            "duration_s": round(r.duration_s, 2),
            "risk": r.risk_level,
            "error": (r.error or "")[:300],
            "stdout_tail": r.stdout_tail[:512],     # cap to keep the file small
            "stderr_tail": r.stderr_tail[:512],
        })
    if not rows:
        # Nothing new to append, but still trim if the file has grown past the cap
        # (e.g. from runs written by a previous app session).
        _trim(path)
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    except OSError:
        pass
    _trim(path)


def _trim(path: Path) -> None:
    """Cap the file at MAX_LINES, keeping the newest KEEP."""
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    try:
        path.write_text("\n".join(lines[-KEEP:]) + "\n", "utf-8")
    except OSError:
        pass


def recent(path: Path, n: int = 50) -> list[dict]:
    """Newest-first fleet-run rows. Bad lines are skipped."""
    try:
        lines = path.read_text("utf-8").splitlines()[-n:]
    except OSError:
        return []
    out: list[dict] = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def clear(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
