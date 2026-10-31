"""Threshold alerts: debounce + cooldown engine and a background checker. No UI.

Alerts only run while the app is running; they are not 24/7 monitoring.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from services.monitor import MONITOR_CMD, Metrics, MonitorState, Thresholds, parse_snapshot


@dataclass
class Alert:
    key: str
    title: str
    detail: str
    level: str = "crit"
    ts: float = field(default_factory=time.time)


class AlertEngine:
    """Fires only after `consecutive` bad checks in a row, then at most once per `cooldown` seconds per key."""

    def __init__(self, consecutive: int = 2, cooldown: float = 1800.0) -> None:
        self.consecutive, self.cooldown = consecutive, cooldown
        self.state: dict[str, dict] = {}

    def evaluate(self, m: Metrics, thr: Thresholds, services: dict[str, str], now: Optional[float] = None) -> list[Alert]:
        now = now if now is not None else time.time()
        bad: dict[str, tuple[str, str]] = {}
        if m.cpu is not None and m.cpu >= thr.cpu[1]:
            bad["cpu"] = ("High CPU", f"CPU at {m.cpu:.0f}% (limit {thr.cpu[1]}%)")
        if m.mem_pct >= thr.ram[1]:
            bad["ram"] = ("High memory use", f"RAM at {m.mem_pct:.0f}% (limit {thr.ram[1]}%)")
        for d in m.mounts:
            if d.pct >= thr.disk[1]:
                bad[f"disk:{d.mount}"] = ("Disk almost full", f"{d.mount} is {d.pct}% full (limit {thr.disk[1]}%)")
        for name, st in services.items():
            if st != "active":
                bad[f"svc:{name}"] = ("Service down", f"{name} is {st}")
        fired: list[Alert] = []
        for key, (title, detail) in bad.items():
            s = self.state.setdefault(key, {"count": 0, "last": None})
            s["count"] += 1
            if s["count"] >= self.consecutive and (s["last"] is None or now - s["last"] >= self.cooldown):
                s["last"] = now
                fired.append(Alert(key, title, detail, ts=now))
        for key, s in self.state.items():
            if key not in bad:
                s["count"] = 0                          # recovered; the cooldown still prevents flapping spam
        return fired


def parse_active(names: list[str], text: str) -> dict[str, str]:
    """`systemctl is-active a b c` prints one state per line, in order."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return {n: (lines[i] if i < len(lines) else "unknown") for i, n in enumerate(names)}


class AlertRunner:
    def __init__(self, ssh: Any, interval: Callable[[], float], thresholds: Callable[[], Thresholds],
                 services: Callable[[], list[str]], on_alert: Callable[[Alert], Any],
                 spawn: Callable[[Callable[[], Awaitable]], Any], engine: Optional[AlertEngine] = None,
                 min_delay: float = 5.0) -> None:
        self.ssh, self.interval, self.thresholds, self.services = ssh, interval, thresholds, services
        self.on_alert, self._spawn = on_alert, spawn
        self.engine = engine or AlertEngine()
        self.state = MonitorState()
        self.min_delay = min_delay
        self._active = False
        self._running = False
        self._lock = threading.Lock()

    def set_active(self, active: bool) -> None:
        self._active = active
        if not active:
            self.state.reset()
        with self._lock:
            if self._running or not active:
                return
            self._running = True
        self._spawn(self._loop)

    async def check_once(self) -> list[Alert]:
        if getattr(self.ssh.status, "value", self.ssh.status) != "connected":
            self.state.reset()
            return []
        r = await self.ssh.run(MONITOR_CMD, timeout=20)
        if not r.stdout.strip():
            return []
        m = self.state.update(parse_snapshot(r.stdout))
        svc: dict[str, str] = {}
        names = [n for n in self.services() if n]
        if names:
            from services.systemd import valid_unit
            names = [n for n in names if valid_unit(n)]
            if names:
                out = await self.ssh.run("systemctl is-active " + " ".join(names), timeout=15)
                svc = parse_active(names, out.stdout)
        return self.engine.evaluate(m, self.thresholds(), svc)

    async def _loop(self) -> None:
        try:
            while self._active:
                try:
                    for a in await self.check_once():
                        res = self.on_alert(a)
                        if inspect.isawaitable(res):
                            await res
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(max(self.min_delay, float(self.interval())))
        finally:
            with self._lock:
                self._running = False
