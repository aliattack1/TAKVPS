"""systemd service listing, actions and journal helpers. Pure, no UI."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Optional

LIST_CMD = ("systemctl list-units --type=service --all --no-legend --no-pager --plain 2>/dev/null; "
            "echo '##FILES'; systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null")
VERBS = {"start", "stop", "restart", "reload", "enable", "disable", "mask", "unmask"}
_UNIT = re.compile(r"^[\w@.:\\-]+$")
_BULLETS = "●○×*↻"


@dataclass
class Unit:
    name: str
    load: str = ""
    active: str = ""
    sub: str = ""
    description: str = ""
    enabled: str = ""            # enabled / disabled / static / masked / generated / ... ("" = unknown)

    @property
    def short(self) -> str:
        return self.name[:-8] if self.name.endswith(".service") else self.name

    @property
    def failed(self) -> bool:
        return self.active == "failed"


def parse_units(text: str) -> list[Unit]:
    files: dict[str, str] = {}
    units: dict[str, Unit] = {}
    in_files = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "##FILES":
            in_files = True
            continue
        line = line.lstrip(_BULLETS + " ")
        if not line:
            continue
        parts = line.split(None, 4) if not in_files else line.split()
        if not parts or not parts[0].endswith(".service"):
            continue
        if in_files:
            if len(parts) >= 2:
                files[parts[0]] = parts[1]
        elif len(parts) >= 4:
            units[parts[0]] = Unit(parts[0], parts[1], parts[2], parts[3], parts[4] if len(parts) > 4 else "")
    for name, state in files.items():
        if name in units:
            units[name].enabled = state
        elif not name.endswith("@.service"):
            units[name] = Unit(name, "loaded" if state != "masked" else "masked", "inactive", "dead", "", state)
    for u in units.values():
        if u.load == "masked" and not u.enabled:
            u.enabled = "masked"
    return sorted(units.values(), key=lambda u: (not u.failed, u.name.lower()))


def valid_unit(name: str) -> bool:
    return bool(_UNIT.match(name)) and len(name) < 256


def _unit_arg(unit: str) -> str:
    if not valid_unit(unit):
        raise ValueError("Invalid unit name.")
    return shlex.quote(unit)


def action_command(verb: str, unit: str) -> str:
    if verb not in VERBS:
        raise ValueError("Unknown action.")
    return f"systemctl {verb} {_unit_arg(unit)}"


def status_command(unit: str) -> str:
    return f"systemctl status {_unit_arg(unit)} --no-pager -l"


def journal_command(unit: str, lines: int = 200, follow: bool = False) -> str:
    lines = max(1, min(int(lines), 5000))
    return f"journalctl -u {_unit_arg(unit)} -n {lines} --no-pager" + (" -f" if follow else "")


def fragment_command(unit: str) -> str:
    return f"systemctl show -p FragmentPath --value {_unit_arg(unit)}"


def needs_journal_sudo(output: str) -> bool:
    return bool(re.search(r"insufficient permissions|not seeing messages from other users|No journal files", output, re.I))


DAEMON_RELOAD = "systemctl daemon-reload"
UNIT_TEMPLATE = """[Unit]
Description=My service
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory=/srv/app
ExecStart=/usr/bin/env echo hello
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


def unit_path(name: str) -> Optional[str]:
    n = name if name.endswith((".service", ".timer", ".socket")) else name + ".service"
    return f"/etc/systemd/system/{n}" if valid_unit(n) else None


def parse_timers(text: str) -> list[dict]:
    """Best effort: UNIT and ACTIVATES are the last two columns; the rest is the when/left text."""
    out = []
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[-2].endswith(".timer"):
            out.append({"unit": parts[-2], "activates": parts[-1], "when": " ".join(parts[:-2])})
    return out
