"""Step runner with live output. The SSH object needs `run_stream(cmd, on_chunk, sudo, sudo_password) -> exit`."""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r")
WRONG_SUDO = re.compile(r"incorrect password|Sorry, try again|a password is required|no tty present", re.I)
MAX_OUTPUT = 200_000


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


@dataclass
class Step:
    name: str
    cmd: str
    sudo: bool = False
    ignore_failure: bool = False
    stdin: Optional[str] = None          # e.g. a database password fed to a `read -r` wrapper


@dataclass
class StepResult:
    name: str
    exit: int
    seconds: float


@dataclass
class RunReport:
    results: list[StepResult] = field(default_factory=list)
    ok: bool = True
    failed: Optional[int] = None
    cancelled: bool = False

    @property
    def last_exit(self) -> int:
        return self.results[-1].exit if self.results else -1


class Runner:
    def __init__(self, ssh: Any, on_output: Callable[[str], Any] = lambda s: None,
                 on_step: Callable[[int, Step, str], Any] = lambda i, s, st: None) -> None:
        self.ssh, self.on_output, self.on_step = ssh, on_output, on_step
        self.output = ""
        self.report = RunReport()

    async def _call(self, fn: Callable, *args: Any) -> None:
        res = fn(*args)
        if inspect.isawaitable(res):
            await res

    async def _emit(self, text: str) -> None:
        self.output += text
        if len(self.output) > MAX_OUTPUT:
            self.output = "… (earlier output trimmed) …\n" + self.output[-MAX_OUTPUT // 2:]
        await self._call(self.on_output, text)

    async def run(self, steps: list[Step], sudo_password: Optional[str] = None) -> RunReport:
        self.report = RunReport()
        try:
            for i, st in enumerate(steps):
                await self._call(self.on_step, i, st, "running")
                if len(steps) > 1:
                    await self._emit(f"\n▶ {st.name}\n")
                t0 = time.monotonic()
                code = await self.ssh.run_stream(st.cmd, self._emit, sudo=st.sudo, sudo_password=sudo_password,
                                                 input_text=st.stdin)
                self.report.results.append(StepResult(st.name, code, time.monotonic() - t0))
                ok = code == 0 or st.ignore_failure
                await self._call(self.on_step, i, st, "done" if ok else "failed")
                if not ok:
                    self.report.ok, self.report.failed = False, i
                    break
        except asyncio.CancelledError:
            self.report.ok, self.report.cancelled = False, True
            raise
        except Exception as e:  # noqa: BLE001
            self.report.ok, self.report.failed = False, len(self.report.results)
            await self._emit(f"\n⚠ {getattr(e, 'message', None) or e}\n")
        return self.report
