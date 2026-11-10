"""Crontab parsing/serialising, schedule validation, descriptions, next-run preview. Pure."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

MACROS = {"@reboot": None, "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
          "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DOWS = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}
_NAMES = [None, None, None, _MONTHS, _DOWS]
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DISABLED_MARK = "#takvps-disabled# "


@dataclass
class Schedule:
    minutes: set
    hours: set
    doms: set
    months: set
    dows: set
    dom_star: bool
    dow_star: bool
    reboot: bool = False


def _field(tok: str, idx: int) -> set:
    lo, hi = _RANGES[idx]
    names = _NAMES[idx]

    def conv(x: str) -> int:
        if names and x.lower() in names:
            return names[x.lower()]
        if not x.isdigit():
            raise ValueError(f"“{x}” is not a number")
        return int(x)

    out: set = set()
    for part in tok.split(","):
        if not part:
            raise ValueError("empty list item")
        rng, step = part, 1
        if "/" in part:
            rng, st = part.split("/", 1)
            if not st.isdigit() or int(st) < 1:
                raise ValueError("bad step")
            step = int(st)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            x, y = rng.split("-", 1)
            a, b = conv(x), conv(y)
        else:
            a = conv(rng)
            b = hi if "/" in part else a
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError(f"{part} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    if idx == 4 and 7 in out:
        out.discard(7)
        out.add(0)
    return out


def parse_schedule(expr: str) -> Schedule:
    e = expr.strip()
    if e.startswith("@"):
        if e.lower() not in MACROS:
            raise ValueError(f"Unknown shortcut {e}")
        if e.lower() == "@reboot":
            return Schedule(set(), set(), set(), set(), set(), True, True, reboot=True)
        e = MACROS[e.lower()]
    f = e.split()
    if len(f) != 5:
        raise ValueError("A schedule needs 5 fields: minute hour day month weekday")
    sets = [_field(t, i) for i, t in enumerate(f)]
    return Schedule(sets[0], sets[1], sets[2], sets[3], sets[4], f[2].startswith("*"), f[4].startswith("*"))


def validate_schedule(expr: str) -> Optional[str]:
    try:
        parse_schedule(expr)
        return None
    except ValueError as e:
        return str(e)


def _day_ok(s: Schedule, t: datetime) -> bool:
    dom, dow = t.day in s.doms, (t.isoweekday() % 7) in s.dows
    if s.dom_star and s.dow_star:
        return True
    if s.dom_star:
        return dow
    if s.dow_star:
        return dom
    return dom or dow


def next_runs(expr: str, after: datetime, n: int = 5) -> list[datetime]:
    s = parse_schedule(expr)
    if s.reboot:
        return []
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit, out = after + timedelta(days=800), []
    while len(out) < n and t < limit:
        if t.month not in s.months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif not _day_ok(s, t):
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
        elif t.hour not in s.hours:
            t = t.replace(minute=0) + timedelta(hours=1)
        elif t.minute not in s.minutes:
            t += timedelta(minutes=1)
        else:
            out.append(t)
            t += timedelta(minutes=1)
    return out


def describe(expr: str) -> str:
    e = expr.strip()
    macro = {"@reboot": "At system boot", "@hourly": "Every hour", "@daily": "Every day at 00:00",
             "@midnight": "Every day at 00:00", "@weekly": "Every Sunday at 00:00",
             "@monthly": "On the 1st of every month at 00:00", "@yearly": "Every January 1st at 00:00",
             "@annually": "Every January 1st at 00:00"}.get(e.lower())
    if macro:
        return macro
    f = e.split()
    if len(f) != 5 or validate_schedule(e):
        return "Custom schedule"
    mi, h, dom, mon, dow = f
    num = lambda x: x.isdigit()  # noqa: E731
    at = f"{int(h):02d}:{int(mi):02d}" if num(h) and num(mi) else None
    if f == ["*"] * 5:
        return "Every minute"
    if re.fullmatch(r"\*/\d+", mi) and h == dom == mon == dow == "*":
        return f"Every {mi[2:]} minutes"
    if num(mi) and h == dom == mon == dow == "*":
        return f"Every hour at minute {int(mi)}"
    if re.fullmatch(r"\d+", mi) and re.fullmatch(r"\*/\d+", h) and dom == mon == dow == "*":
        return f"Every {h[2:]} hours at minute {int(mi)}"
    if at and dom == mon == "*":
        if dow == "*":
            return f"Every day at {at}"
        days = sorted(_field(dow, 4))
        if days == [1, 2, 3, 4, 5]:
            return f"Every weekday (Mon–Fri) at {at}"
        return "Every " + ", ".join(DAY_NAMES[d] for d in days) + f" at {at}"
    if at and num(dom) and mon == "*" and dow == "*":
        return f"On day {int(dom)} of every month at {at}"
    return f"Minute {mi}, hour {h}, day-of-month {dom}, month {mon}, weekday {dow}"


def build_schedule(kind: str, **p) -> str:
    if kind == "minutes":
        return f"*/{int(p['n'])} * * * *"
    if kind == "hourly":
        return f"{int(p['minute'])} * * * *"
    if kind == "daily":
        return f"{int(p['minute'])} {int(p['hour'])} * * *"
    if kind == "weekly":
        return f"{int(p['minute'])} {int(p['hour'])} * * {int(p['dow'])}"
    if kind == "monthly":
        return f"{int(p['minute'])} {int(p['hour'])} {int(p['dom'])} * *"
    if kind == "reboot":
        return "@reboot"
    return str(p["expr"]).strip()


# ------------------------------------------------------------------ crontab text model
@dataclass
class Line:
    raw: str
    kind: str = "other"            # "job" | "other"
    schedule: str = ""
    command: str = ""
    enabled: bool = True


_JOB = re.compile(r"^(@\w+|\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.+)$")
_ENV = re.compile(r"^[A-Za-z_]\w*\s*=")


def _job(text: str) -> Optional[tuple[str, str]]:
    m = _JOB.match(text.strip())
    if not m or _ENV.match(text.strip()) or validate_schedule(m.group(1)):
        return None
    return m.group(1), m.group(2).strip()


def parse_crontab(text: str) -> list[Line]:
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith(DISABLED_MARK.strip()):
            j = _job(s[len(DISABLED_MARK.strip()):].strip())
            if j:
                lines.append(Line(raw, "job", j[0], j[1], False))
                continue
        elif s and not s.startswith("#"):
            j = _job(s)
            if j:
                lines.append(Line(raw, "job", j[0], j[1], True))
                continue
        lines.append(Line(raw))
    return lines


def make_job(schedule: str, command: str, enabled: bool = True) -> Line:
    body = f"{schedule.strip()} {command.strip()}"
    return Line((DISABLED_MARK if not enabled else "") + body, "job", schedule.strip(), command.strip(), enabled)


def serialize(lines: list[Line]) -> str:
    text = "\n".join(l.raw for l in lines)
    return text + "\n" if text and not text.endswith("\n") else text


def validate_job(schedule: str, command: str) -> Optional[str]:
    if (err := validate_schedule(schedule)):
        return err
    if not command.strip():
        return "Enter the command to run."
    if "\n" in command:
        return "A cron command must be on one line (use a script file for more)."
    return None
