"""Monitoring: one-round-trip /proc snapshot, client-side deltas, health levels, poller.

No Flet and no asyncssh imports, so everything here is testable with fixture strings.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import shlex
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Optional

HISTORY_LEN = 120

_SCRIPT = r"""
echo '##STAT'; grep '^cpu' /proc/stat
echo '##MEM'; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
echo '##LOAD'; cat /proc/loadavg
echo '##UPTIME'; cat /proc/uptime
echo '##NET'; cat /proc/net/dev
echo '##DISKSTAT'; cat /proc/diskstats 2>/dev/null
echo '##DF'; df -P -k 2>/dev/null | grep '^/dev/'
echo '##USERS'; who 2>/dev/null | wc -l
echo '##PROCS'; ls -d /proc/[0-9]* 2>/dev/null | wc -l
echo '##TEMP'; cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null
echo '##TOPCPU'; ps -eo pid=,user:16=,pcpu=,pmem=,comm= --sort=-pcpu 2>/dev/null | head -n 8
echo '##TOPMEM'; ps -eo pid=,user:16=,pcpu=,pmem=,comm= --sort=-pmem 2>/dev/null | head -n 8
"""
MONITOR_CMD = "sh -c " + shlex.quote(_SCRIPT)

_WHOLE_DISK = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


# ------------------------------------------------------------------ models
@dataclass
class Mount:
    device: str
    mount: str
    size_kb: int
    used_kb: int
    pct: int


@dataclass
class ProcInfo:
    pid: int
    user: str
    cpu: float          # ps %CPU = average since process start, NOT instantaneous
    mem: float
    cmd: str


@dataclass
class Snapshot:
    cpu: dict[str, tuple[int, int]] = field(default_factory=dict)   # name -> (total, idle)
    mem_total_kb: int = 0
    mem_avail_kb: int = 0
    swap_total_kb: int = 0
    swap_free_kb: int = 0
    load: tuple[float, float, float] = (0.0, 0.0, 0.0)
    uptime_s: float = 0.0
    net: dict[str, tuple[int, int]] = field(default_factory=dict)   # iface -> (rx, tx) bytes
    disk_io: dict[str, tuple[int, int]] = field(default_factory=dict)  # dev -> (read, write) bytes
    mounts: list[Mount] = field(default_factory=list)
    users: int = 0
    procs: int = 0
    temp_c: Optional[float] = None
    top_cpu: list[ProcInfo] = field(default_factory=list)
    top_mem: list[ProcInfo] = field(default_factory=list)


@dataclass
class Metrics:
    cpu: Optional[float]                    # None until two samples exist
    cores: list[float]
    mem_total_kb: int
    mem_used_kb: int
    mem_pct: float
    swap_total_kb: int
    swap_used_kb: int
    swap_pct: float
    load: tuple[float, float, float]
    uptime_s: float
    users: int
    procs: int
    temp_c: Optional[float]
    mounts: list[Mount]
    net: dict[str, tuple[float, float]]     # iface -> (down B/s, up B/s)
    net_down: float
    net_up: float
    disk_read: float
    disk_write: float
    top_cpu: list[ProcInfo]
    top_mem: list[ProcInfo]
    taken_at: float


@dataclass
class Thresholds:
    cpu: tuple[int, int] = (70, 90)
    ram: tuple[int, int] = (80, 92)
    disk: tuple[int, int] = (80, 92)


def level(value: Optional[float], pair: tuple[int, int]) -> str:
    if value is None:
        return "ok"
    if value >= pair[1]:
        return "crit"
    if value >= pair[0]:
        return "warn"
    return "ok"


_ORDER = {"ok": 0, "warn": 1, "crit": 2}


def overall_level(m: Metrics, t: Thresholds) -> str:
    levels = [level(m.cpu, t.cpu), level(m.mem_pct, t.ram)]
    levels += [level(x.pct, t.disk) for x in m.mounts]
    return max(levels, key=_ORDER.__getitem__)


# ------------------------------------------------------------------ parsing
def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("##"):
            cur = line[2:].strip()
            out[cur] = []
        elif cur is not None and line.strip():
            out[cur].append(line)
    return out


def _procs(lines: list[str]) -> list[ProcInfo]:
    res = []
    for ln in lines:
        p = ln.split(None, 4)
        if len(p) < 5:
            continue
        try:
            res.append(ProcInfo(int(p[0]), p[1], float(p[2]), float(p[3]), p[4].strip()))
        except ValueError:
            continue
    return res


def parse_snapshot(text: str) -> Snapshot:
    s = _sections(text)
    snap = Snapshot()
    for ln in s.get("STAT", []):
        p = ln.split()
        try:
            vals = [int(x) for x in p[1:9]]
        except ValueError:
            continue
        snap.cpu[p[0]] = (sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0))
    for ln in s.get("MEM", []):
        p = ln.replace(":", " ").split()
        if len(p) >= 2 and p[1].isdigit():
            key = {"MemTotal": "mem_total_kb", "MemAvailable": "mem_avail_kb",
                   "SwapTotal": "swap_total_kb", "SwapFree": "swap_free_kb"}.get(p[0])
            if key:
                setattr(snap, key, int(p[1]))
    try:
        a, b, c = (float(x) for x in s.get("LOAD", [""])[0].split()[:3])
        snap.load = (a, b, c)
    except (ValueError, IndexError):
        pass
    try:
        snap.uptime_s = float(s.get("UPTIME", [""])[0].split()[0])
    except (ValueError, IndexError):
        pass
    for ln in s.get("NET", []):
        if ":" not in ln:
            continue
        name, rest = ln.split(":", 1)
        name = name.strip()
        f = rest.split()
        if name == "lo" or name.startswith("veth") or len(f) < 9:
            continue
        try:
            snap.net[name] = (int(f[0]), int(f[8]))
        except ValueError:
            continue
    for ln in s.get("DISKSTAT", []):
        p = ln.split()
        if len(p) >= 10 and _WHOLE_DISK.match(p[2]):
            try:
                snap.disk_io[p[2]] = (int(p[5]) * 512, int(p[9]) * 512)
            except ValueError:
                continue
    seen = set()
    for ln in s.get("DF", []):
        p = ln.split(None, 5)
        if len(p) < 6 or p[0] in seen:
            continue
        try:
            snap.mounts.append(Mount(p[0], p[5].strip(), int(p[1]), int(p[2]),
                                     int(p[4].rstrip("%"))))
            seen.add(p[0])
        except ValueError:
            continue
    for key, attr in (("USERS", "users"), ("PROCS", "procs")):
        try:
            setattr(snap, attr, int(s.get(key, ["0"])[0]))
        except ValueError:
            pass
    temps = []
    for ln in s.get("TEMP", []):
        try:
            t = int(ln.strip()) / 1000
            if 0 < t < 150:
                temps.append(t)
        except ValueError:
            continue
    snap.temp_c = max(temps) if temps else None
    snap.top_cpu = _procs(s.get("TOPCPU", []))
    snap.top_mem = _procs(s.get("TOPMEM", []))
    return snap


# ------------------------------------------------------------------ state
def _core_key(name: str) -> int:
    return int(name[3:])


class MonitorState:
    """Keeps the previous snapshot and rolling history; turns counters into rates."""

    def __init__(self) -> None:
        self.prev: Optional[Snapshot] = None
        self.last: Optional[Metrics] = None
        self.history: dict[str, Deque[float]] = {
            k: deque(maxlen=HISTORY_LEN) for k in ("cpu", "ram", "down", "up")}

    def reset(self) -> None:
        self.prev = None
        self.last = None
        for d in self.history.values():
            d.clear()

    def update(self, snap: Snapshot, now: Optional[float] = None) -> Metrics:
        prev = self.prev
        if prev is not None and snap.uptime_s < prev.uptime_s:
            prev = None                                   # server rebooted: counters restarted
        dt = (snap.uptime_s - prev.uptime_s) if prev else 0.0
        have_rates = prev is not None and dt > 0

        def cpu_pct(name: str) -> Optional[float]:
            if not have_rates or name not in prev.cpu or name not in snap.cpu:
                return None
            dtot = snap.cpu[name][0] - prev.cpu[name][0]
            didle = snap.cpu[name][1] - prev.cpu[name][1]
            if dtot <= 0 or didle < 0:
                return None
            return max(0.0, min(100.0, (1 - didle / dtot) * 100))

        def rate(cur: int, old: int) -> float:
            return max(0, cur - old) / dt if have_rates else 0.0

        cores = [v for n in sorted((k for k in snap.cpu if k != "cpu"), key=_core_key)
                 if (v := cpu_pct(n)) is not None]
        net = {i: (rate(rx, prev.net[i][0]) if have_rates and i in prev.net else 0.0,
                   rate(tx, prev.net[i][1]) if have_rates and i in prev.net else 0.0)
               for i, (rx, tx) in snap.net.items()}
        dr = sum(rate(r, prev.disk_io[d][0]) for d, (r, _w) in snap.disk_io.items()
                 if have_rates and d in prev.disk_io)
        dw = sum(rate(w, prev.disk_io[d][1]) for d, (_r, w) in snap.disk_io.items()
                 if have_rates and d in prev.disk_io)
        mem_used = max(snap.mem_total_kb - snap.mem_avail_kb, 0)
        swap_used = max(snap.swap_total_kb - snap.swap_free_kb, 0)
        m = Metrics(
            cpu=cpu_pct("cpu"), cores=cores,
            mem_total_kb=snap.mem_total_kb, mem_used_kb=mem_used,
            mem_pct=(mem_used / snap.mem_total_kb * 100) if snap.mem_total_kb else 0.0,
            swap_total_kb=snap.swap_total_kb, swap_used_kb=swap_used,
            swap_pct=(swap_used / snap.swap_total_kb * 100) if snap.swap_total_kb else 0.0,
            load=snap.load, uptime_s=snap.uptime_s, users=snap.users, procs=snap.procs,
            temp_c=snap.temp_c, mounts=snap.mounts, net=net,
            net_down=sum(v[0] for v in net.values()), net_up=sum(v[1] for v in net.values()),
            disk_read=dr, disk_write=dw, top_cpu=snap.top_cpu, top_mem=snap.top_mem,
            taken_at=now if now is not None else time.time())
        if m.cpu is not None:
            self.history["cpu"].append(m.cpu)
            self.history["ram"].append(m.mem_pct)
            self.history["down"].append(m.net_down)
            self.history["up"].append(m.net_up)
        self.prev, self.last = snap, m
        return m


# ------------------------------------------------------------------ poller
class MonitorPoller:
    """Polls only while active (page visible). Never overlaps polls; backs off on errors.

    `spawn(fn)` starts the coroutine function `fn` on the app's event loop
    (Flet's `page.run_task`)."""

    def __init__(self, ssh: Any, state: MonitorState, interval: Callable[[], float],
                 on_update: Callable[[Metrics], Any], on_error: Callable[[str], Any],
                 spawn: Callable[[Callable[[], Awaitable]], Any],
                 min_delay: float = 0.2) -> None:
        self.ssh, self.state, self.interval = ssh, state, interval
        self.min_delay = min_delay
        self.on_update, self.on_error, self._spawn = on_update, on_error, spawn
        self._active = False
        self._running = False
        self._lock = threading.Lock()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._ensure()

    def _ensure(self) -> None:
        with self._lock:
            if self._running or not self._active:
                return
            self._running = True
        self._spawn(self._loop)

    async def _call(self, fn: Callable, *args: Any) -> None:
        res = fn(*args)
        if inspect.isawaitable(res):
            await res

    async def poll_once(self) -> None:
        if getattr(self.ssh.status, "value", self.ssh.status) != "connected":
            return
        r = await self.ssh.run(MONITOR_CMD, timeout=15)
        if not r.stdout.strip():
            raise RuntimeError(r.stderr.strip() or "Server returned no data")
        await self._call(self.on_update, self.state.update(parse_snapshot(r.stdout)))

    async def _loop(self) -> None:
        try:
            failures = 0
            while self._active:
                t0 = time.monotonic()
                try:
                    await self.poll_once()
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    failures += 1
                    await self._call(self.on_error, getattr(e, "message", None) or str(e) or "Monitoring failed")
                delay = max(self.min_delay, self.interval() - (time.monotonic() - t0))
                await asyncio.sleep(min(delay * (2 ** min(failures, 3)), 30) if failures else delay)
        finally:
            with self._lock:
                self._running = False
            self._ensure()
