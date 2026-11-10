"""Multi-server fleet actions.

Run a snippet (or a one-off command) across N saved servers in parallel, with
a bounded `asyncio.Semaphore` so we don't exhaust file descriptors on hosts
with many saved servers, and a Stop-all button that cancels every still-running
task. Each per-server run goes through the existing risk classifier + the
caller's approval gate, so fleet actions stay under the same safety net as
single-server runs.

Pure state machine + command builders — no UI imports, no Flet imports, no
asyncssh imports. The caller supplies:
  - `connect_and_run(profile, command, sudo, timeout) → RunOutcome` — an async
    callable that opens a throwaway SSH connection (using the existing
    `ui.pages.fleet.probe` pattern) and runs the command, returning our
    `RunOutcome` dataclass.
  - `now()` — `time.time` by default; passed in so tests can be deterministic.
"""
from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

from services.risk import Risk, classify


# ---- per-run state machine -------------------------------------------------

class RunState(str, Enum):
    PENDING = "pending"        # queued behind the semaphore
    CONNECTING = "connecting"  # dialing / authenticating
    RUNNING = "running"        # command executing on the server
    DONE = "done"              # exit 0
    FAILED = "failed"          # exit != 0
    ERROR = "error"            # couldn't connect or run (host key, network, etc.)
    SKIPPED = "skipped"        # preflight refused (unknown host key, missing pw, risk)
    CANCELLED = "cancelled"    # user clicked Stop-all


# Aggregate "did the whole fleet run succeed?" outcomes for the summary.
AGG_OK = {"done"}
AGG_HARD_FAIL = {"failed", "error", "cancelled"}
AGG_SOFT_SKIP = {"skipped"}


@dataclass
class RunOutcome:
    """What `connect_and_run` returns to us. Mirrors `core.ssh_client.RunResult`
    plus an `error` field for connection-time failures."""
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: str = ""            # non-empty when we couldn't connect or run at all


@dataclass
class FleetRun:
    """One server's slice of a fleet run. Updated as the state machine progresses."""
    profile_id: str
    profile_name: str
    target: str                # host:port, for the live dialog
    state: RunState = RunState.PENDING
    exit_code: Optional[int] = None
    stdout_tail: str = ""      # last ~2 KB, for the per-row expander
    stderr_tail: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_s: float = 0.0
    # The classified risk (filled at preflight time so the UI can show a badge).
    risk_level: str = "safe"
    risk_explanation: str = ""
    # The exact command actually run (after variable substitution, before any
    # sudo wrapping). Recorded so the activity log + history dialog can show it.
    command: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in AGG_HARD_FAIL or self.state in AGG_OK or self.state == RunState.SKIPPED

    @property
    def succeeded(self) -> bool:
        return self.state == RunState.DONE

    def summary(self) -> str:
        """One-line label for the live dialog row."""
        if self.state == RunState.PENDING:
            return "Queued"
        if self.state == RunState.CONNECTING:
            return "Connecting…"
        if self.state == RunState.RUNNING:
            return "Running…"
        if self.state == RunState.DONE:
            return f"Done ({self.duration_s:.1f}s, exit 0)"
        if self.state == RunState.FAILED:
            return f"Failed (exit {self.exit_code}, {self.duration_s:.1f}s)"
        if self.state == RunState.ERROR:
            return f"Error: {self.error or 'unreachable'}"
        if self.state == RunState.SKIPPED:
            return f"Skipped: {self.error}"
        if self.state == RunState.CANCELLED:
            return "Cancelled"
        return self.state.value


# ---- fleet-run aggregate ---------------------------------------------------

@dataclass
class FleetRunResult:
    """The summary returned by `FleetRunner.run`. Used for the toast + activity log."""
    started_at: float
    finished_at: float
    command: str
    runs: list[FleetRun] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.runs)

    @property
    def done(self) -> int:
        return sum(1 for r in self.runs if r.state == RunState.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.runs if r.state in AGG_HARD_FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.runs if r.state == RunState.SKIPPED)

    @property
    def all_succeeded(self) -> bool:
        return self.total > 0 and self.done == self.total

    def toast(self) -> str:
        """One-line summary for the post-run toast."""
        if not self.runs:
            return "No servers matched."
        if self.all_succeeded:
            return f"All {self.total} server(s) succeeded."
        parts = [f"{self.done} done", f"{self.failed} failed"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        return " · ".join(parts) + f"  ({self.total} total)"


# ---- the runner ------------------------------------------------------------

# Type of the caller-supplied "connect and run one command on one server" hook.
# Returns a RunOutcome. The runner never touches asyncssh directly.
ConnectAndRun = Callable[["FleetRun", str, bool, float], Awaitable[RunOutcome]]
# Type of the caller-supplied "should this server be included?" preflight.
# Returns (ok, reason_if_skipped).
Preflight = Callable[["FleetRun"], tuple[bool, str]]
# Type of the caller-supplied "ask the user about a risky command" gate.
# Returns True to proceed.
ApprovalGate = Callable[[Risk, "FleetRun"], Awaitable[bool]]


class FleetRunner:
    """Runs a command across N servers in parallel with a bounded concurrency cap.

    The runner is purely a coordinator: it doesn't know about asyncssh, Flet, or
    the vault. All of those concerns are injected via the `connect_and_run`,
    `preflight` and (optional) `gate` callables supplied by the UI layer.

    Cancellation: `cancel()` flips a flag; in-flight `connect_and_run` coroutines
    are cancelled via `asyncio.Task.cancel()`. Pending tasks behind the
    semaphore are marked CANCELLED without ever running.
    """

    def __init__(self, *,
                 connect_and_run: ConnectAndRun,
                 preflight: Optional[Preflight] = None,
                 gate: Optional[ApprovalGate] = None,
                 concurrency: int = 4,
                 per_run_timeout: float = 90.0,
                 now: Callable[[], float] = time.time) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._connect_and_run = connect_and_run
        self._preflight = preflight or (lambda r: (True, ""))
        self._gate = gate
        self._sem = asyncio.Semaphore(concurrency)
        self._per_run_timeout = per_run_timeout
        self._now = now
        self._cancelled = False
        self._tasks: dict[str, asyncio.Task] = {}    # profile_id → task

    def cancel(self) -> None:
        """Stop-all: cancel every in-flight task and mark pending ones CANCELLED."""
        self._cancelled = True
        for t in list(self._tasks.values()):
            if not t.done():
                t.cancel()

    async def run(self, command: str, profiles: list, *,
                  sudo: bool = False) -> FleetRunResult:
        """Run `command` (already variable-substituted) on every profile.

        `profiles` is a list of objects with `.id`, `.name`, `.target` attributes
        (the existing `ConnectionProfile` matches).

        Returns a `FleetRunResult` once every task has terminated.
        """
        self._cancelled = False
        self._tasks = {}
        started = self._now()
        runs: list[FleetRun] = []
        for p in profiles:
            r = FleetRun(
                profile_id=p.id, profile_name=p.name, target=p.target,
                command=command,
            )
            # Pre-gate: classify once so the badge shows before the task starts.
            risk = classify(command)
            r.risk_level = risk.level
            r.risk_explanation = risk.explanation
            runs.append(r)

        async def run_one(r: FleetRun) -> None:
            # Preflight: skip servers with unknown host keys / missing passwords.
            ok, reason = self._preflight(r)
            if not ok:
                r.state = RunState.SKIPPED
                r.error = reason
                r.finished_at = self._now()
                return
            if self._cancelled:
                r.state = RunState.CANCELLED
                r.finished_at = self._now()
                return
            # Approval gate: ask the user once per run (the UI may coalesce).
            if self._gate is not None:
                try:
                    approved = await self._gate(classify(command), r)
                except Exception:  # noqa: BLE001
                    approved = False
                if not approved:
                    r.state = RunState.SKIPPED
                    r.error = "User declined"
                    r.finished_at = self._now()
                    return
            async with self._sem:
                if self._cancelled:
                    r.state = RunState.CANCELLED
                    r.finished_at = self._now()
                    return
                r.state = RunState.CONNECTING
                r.started_at = self._now()
                try:
                    # `connect_and_run` is responsible for splitting its own
                    # budget between connect + run. The wait_for here is the
                    # overall per-server cap so a hung server doesn't stall
                    # the whole fleet run.
                    outcome = await asyncio.wait_for(
                        self._connect_and_run(r, command, sudo, self._per_run_timeout),
                        self._per_run_timeout,
                    )
                except asyncio.TimeoutError:
                    r.state = RunState.ERROR
                    r.error = f"Timed out after {self._per_run_timeout:.0f}s"
                    r.finished_at = self._now()
                    r.duration_s = r.finished_at - r.started_at
                    return
                except asyncio.CancelledError:
                    r.state = RunState.CANCELLED
                    r.finished_at = self._now()
                    r.duration_s = r.finished_at - r.started_at
                    return
                except Exception as e:  # noqa: BLE001
                    r.state = RunState.ERROR
                    r.error = getattr(e, "message", None) or str(e) or "run failed"
                    r.finished_at = self._now()
                    r.duration_s = r.finished_at - r.started_at
                    return
                r.finished_at = self._now()
                r.duration_s = r.finished_at - r.started_at
                if outcome.error:
                    r.state = RunState.ERROR
                    r.error = outcome.error
                elif outcome.exit_code == 0:
                    r.state = RunState.DONE
                    r.exit_code = 0
                else:
                    r.state = RunState.FAILED
                    r.exit_code = outcome.exit_code
                # Keep the last ~2 KB of each stream for the live dialog's expander.
                r.stdout_tail = outcome.stdout[-2048:]
                r.stderr_tail = outcome.stderr[-2048:]

        # Spin up one task per server; remember them so cancel() can reach in.
        for r in runs:
            self._tasks[r.profile_id] = asyncio.ensure_future(run_one(r))
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        finished = self._now()
        return FleetRunResult(started_at=started, finished_at=finished,
                              command=command, runs=runs)


# ---- helpers used by the UI -----------------------------------------------

def order_for_display(runs: list[FleetRun]) -> list[FleetRun]:
    """Order the rows so the user sees the interesting ones first:
    failed/error/cancelled, then done, then still-running, then skipped."""
    rank = {
        RunState.FAILED: 0, RunState.ERROR: 1, RunState.CANCELLED: 2,
        RunState.DONE: 3,
        RunState.RUNNING: 4, RunState.CONNECTING: 5, RunState.PENDING: 6,
        RunState.SKIPPED: 7,
    }
    return sorted(runs, key=lambda r: (rank.get(r.state, 99), r.profile_name.lower()))


def render_command_for_log(command: str, max_len: int = 2000) -> str:
    """Truncate + collapse whitespace for the activity log's `cmd` field."""
    s = " ".join(command.split())
    return s[:max_len]


def snippet_command_for_fleet(snippet) -> tuple[str, bool, Risk]:
    """Given a `services.snippets.Snippet`, return (command, sudo, risk).

    The command is the raw template — variable substitution happens earlier
    (the caller asked for values once and reused them for the whole fleet).
    """
    cmd = snippet.command.strip()
    sudo = bool(snippet.sudo)
    return cmd, sudo, classify(cmd)


def select_profiles(profiles: list, ids: Optional[list[str]] = None,
                    tag: Optional[str] = None) -> list:
    """Filter the saved profiles by id list, by tag, or all (default).

    `tag` is matched against the profile's `.tags` attribute if present.
    """
    if ids:
        idset = set(ids)
        return [p for p in profiles if p.id in idset]
    if tag:
        return [p for p in profiles if tag in getattr(p, "tags", [])]
    return list(profiles)
