"""Reusable commands: model, {{variables}}, safe script building. Pure, no UI."""
from __future__ import annotations

import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Snippet:
    name: str
    command: str
    cwd: str = ""
    sudo: bool = False
    background: bool = False
    group: str = ""
    icon: str = "TERMINAL"
    color: str = "#3B82F6"
    scope: str = ""                  # "" = global, otherwise a connection id
    order: int = 0
    id: str = field(default_factory=new_id)


_VAR = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}")
MAX_COMMAND = 20_000


def find_variables(command: str) -> list[str]:
    """Unique placeholder names in order of first appearance."""
    seen: list[str] = []
    for m in _VAR.finditer(command):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def render(command: str, values: dict[str, str]) -> str:
    """Substitute {{name}} with shell-quoted values. Don't wrap placeholders in quotes yourself."""
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(name)
        return shlex.quote(values[name])
    return _VAR.sub(sub, command)


def cd_target(cwd: str) -> str:
    cwd = cwd.strip()
    if cwd in ("~", "~/"):
        return "~"
    if cwd.startswith("~/"):
        return "~/" + shlex.quote(cwd[2:])
    return shlex.quote(cwd)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "task"


LOG_DIR = "/tmp/takvps-logs"


def build_script(cwd: str, command: str, background: bool = False, name: str = "",
                 ts: Optional[int] = None) -> str:
    """The remote script for a snippet: optional `cd`, optional nohup background wrapper."""
    body = command.strip("\n")
    script = f"cd {cd_target(cwd)} && {{\n{body}\n}}" if cwd.strip() else body
    if not background:
        return script
    log = f"{LOG_DIR}/{_slug(name)}-{ts if ts is not None else int(time.time())}.log"
    q = shlex.quote
    return (f"mkdir -p {LOG_DIR} && nohup sh -c {q(script)} > {q(log)} 2>&1 < /dev/null & "
            f"echo \"Started in background (PID $!). Log: {log}\"")


def validate(sn: Snippet) -> Optional[str]:
    if not sn.name.strip():
        return "Please enter a name."
    if not sn.command.strip():
        return "Please enter a command."
    if len(sn.command) > MAX_COMMAND:
        return "That command is too long."
    return None
