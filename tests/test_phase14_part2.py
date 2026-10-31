"""Phase 14 Part 2 tests: fleet runner + fleet run history (pure logic).

The runner is exercised with fake `connect_and_run` coroutines so we can drive
every state-machine branch (success, fail, error, timeout, skip, cancel)
without a network or asyncssh.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.fleet_runner import (FleetRun, FleetRunner, FleetRunResult, RunOutcome,
                                    RunState, order_for_display, render_command_for_log,
                                    select_profiles, snippet_command_for_fleet)
from storage import fleet_history


# ---- fakes -----------------------------------------------------------------

class FakeProfile:
    def __init__(self, i: int, name: str = "", tags: list = None) -> None:
        self.id = f"s{i}"
        self.name = name or f"Server {i}"
        self.target = f"10.0.0.{i}:22"
        self.tags = tags or []


async def ok_connect(run: FleetRun, cmd: str, sudo: bool, timeout: float) -> RunOutcome:
    await asyncio.sleep(0.005)
    return RunOutcome(stdout=f"ok {run.profile_name}", exit_code=0)


async def fail_connect(run: FleetRun, cmd: str, sudo: bool, timeout: float) -> RunOutcome:
    await asyncio.sleep(0.005)
    return RunOutcome(stderr="boom", exit_code=2)


async def unreachable_connect(run: FleetRun, cmd: str, sudo: bool, timeout: float) -> RunOutcome:
    return RunOutcome(error="Host key not trusted yet. Connect once from Servers.")


async def slow_connect(run: FleetRun, cmd: str, sudo: bool, timeout: float) -> RunOutcome:
    # Sleep longer than the timeout so we exercise the wait_for path.
    await asyncio.sleep(timeout + 0.5)
    return RunOutcome(exit_code=0)


# ======================================================================
# Phase 14 Part 2 — FleetRunner state machine
# ======================================================================
class TestFleetRunnerSuccess(unittest.TestCase):
    def test_all_succeed(self):
        runner = FleetRunner(connect_and_run=ok_connect, per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(i) for i in range(3)]))
        self.assertEqual(result.total, 3)
        self.assertEqual(result.done, 3)
        self.assertEqual(result.failed, 0)
        self.assertTrue(result.all_succeeded)
        self.assertIn("All 3", result.toast())

    def test_one_fails_other_succeeds(self):
        profiles = [FakeProfile(0), FakeProfile(1), FakeProfile(2)]
        async def mixed(run, cmd, sudo, timeout):
            if run.profile_id == "s1":
                return await fail_connect(run, cmd, sudo, timeout)
            return await ok_connect(run, cmd, sudo, timeout)
        runner = FleetRunner(connect_and_run=mixed, per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", profiles))
        self.assertEqual(result.done, 2)
        self.assertEqual(result.failed, 1)
        self.assertFalse(result.all_succeeded)
        self.assertIn("2 done", result.toast())
        self.assertIn("1 failed", result.toast())

    def test_stdout_tail_capped(self):
        async def chatty(run, cmd, sudo, timeout):
            return RunOutcome(stdout="x" * 5000, exit_code=0)
        runner = FleetRunner(connect_and_run=chatty, per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(0)]))
        # Tail is capped at 2048 chars.
        self.assertLessEqual(len(result.runs[0].stdout_tail), 2048)
        self.assertTrue(result.runs[0].stdout_tail.endswith("x"))


class TestFleetRunnerTimeout(unittest.TestCase):
    def test_per_run_timeout_produces_error(self):
        runner = FleetRunner(connect_and_run=slow_connect, per_run_timeout=0.1)
        result = asyncio.run(runner.run("uptime", [FakeProfile(0)]))
        self.assertEqual(result.runs[0].state, RunState.ERROR)
        self.assertIn("Timed out", result.runs[0].error)
        self.assertEqual(result.failed, 1)


class TestFleetRunnerPreflight(unittest.TestCase):
    def test_preflight_skip_reason_recorded(self):
        def deny_all(r: FleetRun) -> tuple[bool, str]:
            return (False, "No saved password")
        runner = FleetRunner(connect_and_run=ok_connect, preflight=deny_all,
                             per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(i) for i in range(2)]))
        for r in result.runs:
            self.assertEqual(r.state, RunState.SKIPPED)
            self.assertEqual(r.error, "No saved password")
        self.assertEqual(result.skipped, 2)
        # connect_and_run should never have been called for skipped servers.
        self.assertEqual(result.done, 0)
        self.assertEqual(result.failed, 0)

    def test_preflight_allow_runs_normally(self):
        def allow(r: FleetRun) -> tuple[bool, str]:
            return (True, "")
        runner = FleetRunner(connect_and_run=ok_connect, preflight=allow,
                             per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(0)]))
        self.assertEqual(result.runs[0].state, RunState.DONE)


class TestFleetRunnerGate(unittest.TestCase):
    def test_gate_decline_skips(self):
        async def deny(risk, run) -> bool:
            return False
        runner = FleetRunner(connect_and_run=ok_connect, gate=deny, per_run_timeout=2)
        result = asyncio.run(runner.run("rm -rf /tmp/x", [FakeProfile(0)]))
        self.assertEqual(result.runs[0].state, RunState.SKIPPED)
        self.assertEqual(result.runs[0].error, "User declined")

    def test_gate_approve_runs(self):
        async def allow(risk, run) -> bool:
            return True
        runner = FleetRunner(connect_and_run=ok_connect, gate=allow, per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(0)]))
        self.assertEqual(result.runs[0].state, RunState.DONE)

    def test_gate_exception_treated_as_decline(self):
        async def broken(risk, run) -> bool:
            raise RuntimeError("gate crashed")
        runner = FleetRunner(connect_and_run=ok_connect, gate=broken, per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(0)]))
        self.assertEqual(result.runs[0].state, RunState.SKIPPED)


class TestFleetRunnerCancel(unittest.TestCase):
    def test_cancel_marks_pending_tasks(self):
        started = asyncio.Event()
        async def blocking(run, cmd, sudo, timeout):
            started.set()
            await asyncio.sleep(10)
            return RunOutcome(exit_code=0)
        profiles = [FakeProfile(i) for i in range(5)]
        runner = FleetRunner(connect_and_run=blocking, concurrency=1, per_run_timeout=30)

        async def driver():
            task = asyncio.ensure_future(runner.run("uptime", profiles))
            await started.wait()
            runner.cancel()
            return await task

        result = asyncio.run(driver())
        states = [r.state for r in result.runs]
        # First task was cancelled mid-run; the rest never started.
        self.assertIn(RunState.CANCELLED, states)
        self.assertEqual(result.failed, 1)   # cancelled counts as failed
        self.assertEqual(result.done, 0)


class TestFleetRunnerError(unittest.TestCase):
    def test_connect_error_recorded(self):
        runner = FleetRunner(connect_and_run=unreachable_connect, per_run_timeout=2)
        result = asyncio.run(runner.run("uptime", [FakeProfile(0)]))
        self.assertEqual(result.runs[0].state, RunState.ERROR)
        self.assertIn("Host key", result.runs[0].error)


class TestFleetRunnerConcurrency(unittest.TestCase):
    def test_concurrency_cap_respected(self):
        # Track the high-water mark of in-flight coroutines.
        in_flight = 0
        peak = 0
        lock = asyncio.Lock()

        async def tracked(run, cmd, sudo, timeout):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return RunOutcome(exit_code=0)

        runner = FleetRunner(connect_and_run=tracked, concurrency=3, per_run_timeout=5)
        result = asyncio.run(runner.run("uptime", [FakeProfile(i) for i in range(8)]))
        self.assertEqual(result.done, 8)
        self.assertLessEqual(peak, 3, f"concurrency cap violated: peak={peak}")

    def test_concurrency_zero_raises(self):
        with self.assertRaises(ValueError):
            FleetRunner(connect_and_run=ok_connect, concurrency=0)


class TestFleetRunnerRisk(unittest.TestCase):
    def test_risk_classified_at_preflight(self):
        runner = FleetRunner(connect_and_run=ok_connect, per_run_timeout=2)
        result = asyncio.run(runner.run("rm -rf /", [FakeProfile(0)]))
        # `rm -rf /` is dangerous; the badge must say so.
        self.assertEqual(result.runs[0].risk_level, "dangerous")
        self.assertTrue(result.runs[0].risk_explanation)


# ======================================================================
# Phase 14 Part 2 — ordering + helpers
# ======================================================================
class TestOrderForDisplay(unittest.TestCase):
    def test_failed_first_then_done_then_running_then_skipped(self):
        runs = [
            FleetRun("a", "Alpha", "", state=RunState.DONE),
            FleetRun("b", "Bravo", "", state=RunState.FAILED),
            FleetRun("c", "Charlie", "", state=RunState.PENDING),
            FleetRun("d", "Delta", "", state=RunState.SKIPPED),
            FleetRun("e", "Echo", "", state=RunState.ERROR),
        ]
        ordered = order_for_display(runs)
        self.assertEqual([r.profile_name for r in ordered],
                         ["Bravo", "Echo", "Alpha", "Charlie", "Delta"])

    def test_ties_break_alphabetically(self):
        runs = [
            FleetRun("z", "Zulu", "", state=RunState.DONE),
            FleetRun("a", "Alpha", "", state=RunState.DONE),
        ]
        ordered = order_for_display(runs)
        self.assertEqual([r.profile_name for r in ordered], ["Alpha", "Zulu"])


class TestRenderCommandForLog(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(render_command_for_log("uptime   &&\n  echo hi"),
                         "uptime && echo hi")

    def test_truncates_long_commands(self):
        out = render_command_for_log("x" * 5000, max_len=100)
        self.assertEqual(len(out), 100)


class TestSnippetCommandForFleet(unittest.TestCase):
    def test_extracts_command_sudo_and_risk(self):
        from services.snippets import Snippet
        sn = Snippet(name="Disk check", command="df -h", sudo=True)
        cmd, sudo, risk = snippet_command_for_fleet(sn)
        self.assertEqual(cmd, "df -h")
        self.assertTrue(sudo)
        self.assertEqual(risk.level, "safe")


class TestSelectProfiles(unittest.TestCase):
    def setUp(self):
        self.profiles = [
            FakeProfile(0, name="web", tags=["prod", "web"]),
            FakeProfile(1, name="db", tags=["prod", "db"]),
            FakeProfile(2, name="dev", tags=["dev"]),
        ]

    def test_select_all(self):
        self.assertEqual(len(select_profiles(self.profiles)), 3)

    def test_select_by_ids(self):
        out = select_profiles(self.profiles, ids=["s0", "s2"])
        self.assertEqual([p.id for p in out], ["s0", "s2"])

    def test_select_by_tag(self):
        out = select_profiles(self.profiles, tag="prod")
        self.assertEqual(len(out), 2)
        self.assertEqual({p.id for p in out}, {"s0", "s1"})

    def test_select_unknown_tag_returns_empty(self):
        self.assertEqual(select_profiles(self.profiles, tag="nope"), [])


class TestFleetRunProperties(unittest.TestCase):
    def test_summary_strings(self):
        for state, expected in [
            (RunState.PENDING, "Queued"),
            (RunState.CONNECTING, "Connecting…"),
            (RunState.RUNNING, "Running…"),
            (RunState.DONE, "Done"),
            (RunState.FAILED, "Failed"),
            (RunState.ERROR, "Error"),
            (RunState.SKIPPED, "Skipped"),
            (RunState.CANCELLED, "Cancelled"),
        ]:
            r = FleetRun("x", "X", "", state=state)
            self.assertIn(expected, r.summary(), f"summary for {state!r}")

    def test_is_terminal(self):
        for s in [RunState.DONE, RunState.FAILED, RunState.ERROR,
                  RunState.SKIPPED, RunState.CANCELLED]:
            self.assertTrue(FleetRun("x", "X", "", state=s).is_terminal,
                            f"{s!r} should be terminal")
        for s in [RunState.PENDING, RunState.CONNECTING, RunState.RUNNING]:
            self.assertFalse(FleetRun("x", "X", "", state=s).is_terminal,
                             f"{s!r} should not be terminal")


# ======================================================================
# Phase 14 Part 2 — fleet run history (storage)
# ======================================================================
class TestFleetHistory(unittest.TestCase):
    def test_append_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.jsonl"
            # Build a fake FleetRunResult-shaped object — we don't import the
            # real class so the storage module stays decoupled.
            class FakeRun:
                profile_id = "s0"
                profile_name = "Server 0"
                target = "10.0.0.0:22"
                class _S:
                    value = "done"
                state = _S()
                exit_code = 0
                duration_s = 1.5
                risk_level = "safe"
                error = ""
                stdout_tail = "ok"
                stderr_tail = ""

            class FakeResult:
                started_at = 1000.0
                finished_at = 1001.5
                command = "uptime"
                runs = [FakeRun(), FakeRun()]

            fleet_history.append_run(path, FakeResult(), now=1234.0)
            self.assertTrue(path.exists())
            rows = fleet_history.recent(path, 10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["conn"], "s0")
            self.assertEqual(rows[0]["command"], "uptime")
            self.assertEqual(rows[0]["state"], "done")
            self.assertEqual(rows[0]["ts"], 1234.0)

    def test_recent_returns_empty_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fleet_history.recent(Path(tmp) / "nope.jsonl", 5), [])

    def test_clear_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.jsonl"
            path.write_text('{"ts":1}\n')
            fleet_history.clear(path)
            self.assertFalse(path.exists())
            # Clearing a missing file is a no-op (no exception).
            fleet_history.clear(path)

    def test_corrupt_line_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.jsonl"
            path.write_text('{"ts":1,"conn":"a"}\nnot json\n{"ts":2,"conn":"b"}\n')
            rows = fleet_history.recent(path, 10)
            # Newest-first; the corrupt line is dropped, the other two survive.
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["conn"], "b")

    def test_trim_caps_at_max_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.jsonl"
            # Write MAX_LINES + 100 valid lines.
            path.write_text("".join(f'{{"ts":{i},"conn":"s{i}"}}\n'
                                   for i in range(fleet_history.MAX_LINES + 100)))
            # append_run trims after writing.
            class FakeResult:
                started_at = 1.0; finished_at = 2.0; command = "uptime"
                runs = []
            fleet_history.append_run(path, FakeResult(), now=9999.0)
            line_count = len(path.read_text("utf-8").splitlines())
            self.assertLessEqual(line_count, fleet_history.MAX_LINES)

    def test_empty_runs_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.jsonl"
            class FakeResult:
                started_at = 1.0; finished_at = 2.0; command = "uptime"
                runs = []
            fleet_history.append_run(path, FakeResult(), now=1.0)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
