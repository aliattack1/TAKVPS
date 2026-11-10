"""Multi-server overview: one light read-only command per server, parsed into a health status. Pure."""
from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field
from typing import Optional

from services.monitor import Thresholds, level

_SCRIPT = r"""
echo '##HOST'; hostname
echo '##LOAD'; cat /proc/loadavg
echo '##CORES'; nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo
echo '##MEM'; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
echo '##DISK'; df -Pk / | tail -n 1
echo '##UP'; cat /proc/uptime
echo '##FAILED'; systemctl --failed --no-legend --plain 2>/dev/null | wc -l
echo '##REBOOT'; [ -f /var/run/reboot-required ] && echo yes || echo no
"""
LIGHT_CMD = "sh -c " + shlex.quote(_SCRIPT)


@dataclass
class FleetStatus:
    ok: bool = False
    error: str = ""
    host: str = ""
    cpu_pct: float = 0.0           # load average / cores (a quick proxy, not instantaneous usage)
    load1: float = 0.0
    cores: int = 1
    mem_pct: float = 0.0
    disk_pct: int = 0
    uptime_s: float = 0.0
    failed: int = 0
    reboot: bool = False
    taken_at: float = field(default_factory=time.time)


def parse_light(text: str) -> FleetStatus:
    sec: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:]
            sec[cur] = []
        elif cur and ln.strip():
            sec[cur].append(ln.strip())
    g = lambda k: (sec.get(k) or [""])[0]  # noqa: E731
    try:
        load1 = float(g("LOAD").split()[0])
        cores = max(1, int(g("CORES") or 1))
    except (ValueError, IndexError):
        return FleetStatus(error="Unexpected response from the server.")
    mem = {}
    for ln in sec.get("MEM", []):
        p = ln.replace(":", " ").split()
        if len(p) >= 2 and p[1].isdigit():
            mem[p[0]] = int(p[1])
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    try:
        disk = int(g("DISK").split()[4].rstrip("%"))
    except (IndexError, ValueError):
        disk = 0
    try:
        up = float(g("UP").split()[0])
    except (IndexError, ValueError):
        up = 0.0
    return FleetStatus(ok=True, host=g("HOST"), load1=load1, cores=cores, cpu_pct=load1 / cores * 100,
                       mem_pct=((total - avail) / total * 100) if total else 0.0, disk_pct=disk, uptime_s=up,
                       failed=int(g("FAILED") or 0) if g("FAILED").isdigit() else 0, reboot=g("REBOOT") == "yes")


_ORDER = {"ok": 0, "warn": 1, "crit": 2, "down": 3}


def health(s: Optional[FleetStatus], thr: Thresholds) -> str:
    if s is None or not s.ok:
        return "down"
    levels = [level(min(s.cpu_pct, 100), thr.cpu), level(s.mem_pct, thr.ram), level(s.disk_pct, thr.disk)]
    if s.failed:
        levels.append("warn")
    return max(levels, key=_ORDER.__getitem__)
