"""Process list parsing, signals and renice. Pure."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

LIST_CMD = "ps -eo pid=,user:16=,pcpu=,pmem=,ni=,stat=,etimes=,args= --sort=-pcpu 2>/dev/null | head -n 400"
SIGNALS = {"TERM": 15, "KILL": 9, "HUP": 1, "INT": 2, "STOP": 19, "CONT": 18}
SIGNAL_HELP = {"TERM": "Ask it to quit cleanly", "KILL": "Force-stop immediately (can't be caught)",
               "HUP": "Reload configuration (hang up)", "INT": "Interrupt (like Ctrl+C)",
               "STOP": "Pause it", "CONT": "Resume a paused process"}


@dataclass
class Proc:
    pid: int
    user: str
    cpu: float
    mem: float
    nice: str
    stat: str
    age: int
    cmd: str

    @property
    def name(self) -> str:
        first = self.cmd.split(" ")[0] if self.cmd else ""
        return first.rsplit("/", 1)[-1] or self.cmd[:30]


def parse_ps(text: str) -> list[Proc]:
    out = []
    for ln in text.splitlines():
        p = ln.split(None, 7)
        if len(p) < 8:
            continue
        try:
            out.append(Proc(int(p[0]), p[1], float(p[2]), float(p[3]), p[4], p[5],
                            int(p[6]) if p[6].isdigit() else 0, p[7].strip()))
        except ValueError:
            continue
    return out


def sort_procs(ps: list[Proc], key: str) -> list[Proc]:
    k = {"cpu": lambda p: -p.cpu, "mem": lambda p: -p.mem, "pid": lambda p: p.pid,
         "name": lambda p: p.name.lower()}.get(key, lambda p: -p.cpu)
    return sorted(ps, key=k)


def filter_procs(ps: list[Proc], query: str) -> list[Proc]:
    q = query.strip().lower()
    return [p for p in ps if not q or q in p.cmd.lower() or q in p.user.lower() or q == str(p.pid)]


def is_critical(p: Proc) -> bool:
    """PID 1, the main sshd daemon (not a per-session 'sshd: user'), kernel threads."""
    if p.pid <= 1 or p.cmd.startswith("["):
        return True
    return bool(re.match(r"^(/\S*/)?sshd\b(?!:)", p.cmd)) and ": " not in p.cmd.split(" ", 1)[0] and \
        not re.match(r"^sshd: ", p.cmd)


def signal_command(pid: int, sig: str) -> str:
    if sig not in SIGNALS:
        raise ValueError("Unknown signal.")
    if int(pid) <= 1:
        raise ValueError("Refusing to signal PID 1 or lower.")
    return f"kill -{SIGNALS[sig]} {int(pid)}"


def renice_command(pid: int, nice: int) -> str:
    if int(pid) <= 1:
        raise ValueError("Refusing to change PID 1.")
    if not -20 <= int(nice) <= 19:
        raise ValueError("Niceness must be between -20 and 19.")
    return f"renice -n {int(nice)} -p {int(pid)}"


def permission_denied(output: str) -> bool:
    return bool(re.search(r"not permitted|permission denied|Operation not permitted", output, re.I))
