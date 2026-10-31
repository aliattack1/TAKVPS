"""Server health score (0-100) from monitoring data, with the reasons and where to fix each. Pure."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

_SCRIPT = r"""
echo '##LOAD'; cat /proc/loadavg
echo '##CORES'; nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo
echo '##MEM'; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
echo '##DISK'; df -Pk 2>/dev/null | grep '^/dev/'
echo '##FAILED'; systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}'
echo '##REBOOT'; [ -f /var/run/reboot-required ] && echo yes || echo no
"""
HEALTH_CMD = "sh -c " + shlex.quote(_SCRIPT)


@dataclass
class HealthInputs:
    disk_pct: int = 0
    disk_mount: str = "/"
    mem_pct: float = 0.0
    swap_pct: float = 0.0
    load_per_core: float = 0.0
    failed: list[str] = field(default_factory=list)
    security: int = 0
    updates: int = 0              # non-security
    reboot: bool = False


@dataclass
class Issue:
    key: str
    title: str
    detail: str
    penalty: int
    fix: str                      # page key to open


def parse_health(text: str) -> HealthInputs:
    sec: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:]
            sec[cur] = []
        elif cur and ln.strip():
            sec[cur].append(ln.strip())
    h = HealthInputs()
    try:
        load1 = float(sec.get("LOAD", [""])[0].split()[0])
        cores = max(1, int((sec.get("CORES") or ["1"])[0]))
        h.load_per_core = load1 / cores
    except (ValueError, IndexError):
        pass
    mem: dict[str, int] = {}
    for ln in sec.get("MEM", []):
        p = ln.replace(":", " ").split()
        if len(p) >= 2 and p[1].isdigit():
            mem[p[0]] = int(p[1])
    if mem.get("MemTotal"):
        h.mem_pct = (mem["MemTotal"] - mem.get("MemAvailable", 0)) / mem["MemTotal"] * 100
    if mem.get("SwapTotal"):
        h.swap_pct = (mem["SwapTotal"] - mem.get("SwapFree", 0)) / mem["SwapTotal"] * 100
    for ln in sec.get("DISK", []):
        p = ln.split(None, 5)
        try:
            pct = int(p[4].rstrip("%"))
        except (IndexError, ValueError):
            continue
        if pct >= h.disk_pct:
            h.disk_pct, h.disk_mount = pct, p[5] if len(p) > 5 else "/"
    h.failed = [x for x in sec.get("FAILED", []) if x.endswith((".service", ".socket", ".timer", ".mount"))]
    h.reboot = (sec.get("REBOOT") or [""])[0] == "yes"
    return h


def _tier(v: float, tiers: list[tuple[float, int]]) -> int:
    for limit, pen in tiers:
        if v >= limit:
            return pen
    return 0


def compute(h: HealthInputs) -> tuple[int, list[Issue]]:
    issues: list[Issue] = []

    def add(key: str, title: str, detail: str, pen: int, fix: str) -> None:
        if pen > 0:
            issues.append(Issue(key, title, detail, pen, fix))

    add("disk", "Disk is filling up", f"{h.disk_mount} is {h.disk_pct}% full",
        _tier(h.disk_pct, [(95, 25), (90, 15), (80, 6)]), "diskusage")
    add("ram", "Memory is under pressure", f"RAM use is {h.mem_pct:.0f}%", _tier(h.mem_pct, [(95, 15), (90, 9), (80, 3)]), "processes")
    add("swap", "Heavy swap use", f"Swap is {h.swap_pct:.0f}% used", 4 if h.swap_pct >= 80 else 0, "processes")
    add("load", "High load", f"Load is {h.load_per_core:.2f} per core",
        _tier(h.load_per_core, [(2.0, 15), (1.0, 8), (0.7, 3)]), "processes")
    if h.failed:
        names = ", ".join(x.rsplit(".", 1)[0] for x in h.failed[:3]) + ("…" if len(h.failed) > 3 else "")
        add("failed", "Failed services", f"{len(h.failed)} failed: {names}", min(24, 8 * len(h.failed)), "services")
    if h.security:
        add("security", "Security updates pending", f"{h.security} security update(s)", min(20, max(5, 5 * h.security)), "packages")
    add("updates", "Updates available", f"{h.updates} package update(s)", _tier(h.updates, [(20, 8), (5, 4), (1, 1)]), "packages")
    if h.reboot:
        add("reboot", "Reboot required", "Updates are installed but need a restart", 6, "packages")
    score = max(0, 100 - sum(i.penalty for i in issues))
    return score, sorted(issues, key=lambda i: -i.penalty)


def rating(score: int) -> str:
    return "Excellent" if score >= 90 else "Good" if score >= 75 else "Needs attention" if score >= 50 else "Critical"
