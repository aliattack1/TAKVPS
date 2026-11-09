"""Persistent terminal sessions via the server's own tmux.

Nothing is installed: if `tmux` is missing, the UI shows a clear "tmux not installed"
message and falls back to the existing in-memory shell. When present, sessions are
created with `tmux new -s <name>` and re-attached with `tmux attach -t <name>`.
Detaching (Ctrl-B then D) leaves the session running on the server, so it survives
SSH drops and you can re-attach later from this app or any other SSH client.

Pure logic only — no UI imports. The SSH object only needs `run(cmd, timeout=)` and
`open_shell(cols, rows, command=)`; both already exist on `core.ssh_client.SSHClient`.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Optional

# A name we accept for a tmux session. tmux itself is permissive (allows most
# printable chars except `:` and `.`), but we restrict to a safe subset so the
# name can always be safely quoted into a shell command without surprises.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
# `tmux list-sessions` lines look like:  "deploy: 1 windows (created Mon Oct 28 ...)"
_SESSION_LINE = re.compile(r"^([^:]+):\s*(\d+)\s*windows.*\((created [^)]+)\)")


@dataclass
class TmuxSession:
    name: str
    windows: int
    created: str
    attached: bool = False     # tmux marks attached sessions with " (attached)"


def sanitize_name(raw: str) -> Optional[str]:
    """Return a safe tmux session name, or None if `raw` can't be used as one."""
    if not raw:
        return None
    name = raw.strip()
    # tmux swaps `.` → `_` and rejects `:`; we just refuse anything outside the
    # safe set so the caller can show a clear error.
    return name if _NAME_RE.match(name) else None


def is_installed(run_result) -> bool:
    """Did `command -v tmux` return a path? `run_result` is a `RunResult`."""
    return bool((run_result.stdout or "").strip()) and (run_result.exit_code or 0) == 0


def parse_sessions(out: str) -> list[TmuxSession]:
    """Parse `tmux list-sessions` output. Empty/error output → []."""
    sessions: list[TmuxSession] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or line.startswith("no server") or line.startswith("error"):
            continue
        m = _SESSION_LINE.match(line)
        if not m:
            continue
        name, windows, created = m.group(1), int(m.group(2)), m.group(3)
        sessions.append(TmuxSession(name, windows, created, attached="(attached)" in line))
    return sessions


def list_command() -> str:
    """List all sessions, exit 0 even when there are none (so callers don't treat
    'no server running' as a hard error)."""
    return "tmux list-sessions 2>/dev/null || true"


def has_session_command(name: str) -> str:
    safe = sanitize_name(name)
    if not safe:
        raise ValueError(f"Invalid tmux session name: {name!r}")
    # `tmux has-session` exits 0 if the session exists, non-zero otherwise.
    return f"tmux has-session -t {shlex.quote(safe)} 2>/dev/null"


def new_session_command(name: str) -> str:
    """Start a new named session in detached mode (no shell attaches yet)."""
    safe = sanitize_name(name)
    if not safe:
        raise ValueError(f"Invalid tmux session name: {name!r}")
    return f"tmux new-session -d -s {shlex.quote(safe)}"


def attach_command(name: str) -> str:
    """Command to hand to `open_shell(command=...)` so the PTY runs `tmux attach`.

    `tmux attach -t name` connects to the existing session. If it doesn't exist,
    tmux exits immediately with a clear error message in the terminal.
    """
    safe = sanitize_name(name)
    if not safe:
        raise ValueError(f"Invalid tmux session name: {name!r}")
    return f"tmux attach -t {shlex.quote(safe)}"


def new_or_attach_command(name: str) -> str:
    """Create the session if missing, then attach to it (the common case)."""
    safe = sanitize_name(name)
    if not safe:
        raise ValueError(f"Invalid tmux session name: {name!r}")
    return f"tmux new-session -A -s {shlex.quote(safe)}"


def kill_session_command(name: str) -> str:
    safe = sanitize_name(name)
    if not safe:
        raise ValueError(f"Invalid tmux session name: {name!r}")
    return f"tmux kill-session -t {shlex.quote(safe)}"


def default_session_name(prefix: str = "takvps") -> str:
    """`takvps1`, `takvps2`, ... — caller increments a counter."""
    return prefix


# Hook for the terminal controller: returns the command string to pass to
# open_shell() for the requested session. None means "use the default shell"
# (the original behavior).
def resolve_shell_command(ssh, name: Optional[str]) -> Optional[str]:
    """If `name` is given and tmux is installed, return the attach command.

    Otherwise return None (caller opens a normal shell). This is called from
    the UI; we keep the actual SSH round-trip there so this stays pure-ish.
    """
    if not name:
        return None
    return new_or_attach_command(name)
