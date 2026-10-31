"""Multi-server dashboard: one light, read-only check per saved server, run in parallel.

Phase 14 Part 2: also lets you run a snippet (or one-off command) across every
saved server in parallel, with a live per-server progress dialog and a Stop-all
button. The runner is in `services/fleet_runner.py`; this file wires it to Flet.
"""
from __future__ import annotations

import asyncio
import time

import flet as ft

from core.errors import HostKeyRejected, TakError
from core.events import EventBus
from core.fmt import human_duration, time_ago
from core.ssh_client import SSHClient
from services.fleet import LIGHT_CMD, FleetStatus, health, parse_light
from services.fleet_runner import (AGG_HARD_FAIL, FleetRun, FleetRunner, FleetRunResult,
                                     RunOutcome, RunState, order_for_display, select_profiles)
from services.risk import classify
from services import snippets as S
from storage.connections import ConnectionConfigError, ConnectionProfile
from storage.fleet_history import append_run as append_fleet_run
from storage.settings import data_dir
from storage.vault import VaultError
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext

_COLOR = {"ok": ft.Colors.GREEN, "warn": ft.Colors.AMBER_700, "crit": ft.Colors.RED, "down": ft.Colors.OUTLINE}
_LABEL = {"ok": "Healthy", "warn": "Warning", "crit": "Critical", "down": "Unreachable"}
_STATE_COLOR = {
    RunState.DONE: ft.Colors.GREEN, RunState.FAILED: ft.Colors.AMBER_700,
    RunState.ERROR: ft.Colors.RED, RunState.SKIPPED: ft.Colors.OUTLINE,
    RunState.CANCELLED: ft.Colors.OUTLINE,
    RunState.PENDING: ft.Colors.ON_SURFACE_VARIANT,
    RunState.CONNECTING: ft.Colors.PRIMARY, RunState.RUNNING: ft.Colors.PRIMARY,
}


async def probe(ctx: AppContext, p: ConnectionProfile) -> FleetStatus:
    """Connect on a throwaway client (never prompts: unknown host keys and missing passwords are skipped)."""
    try:
        creds = ctx.conns.credentials(p, ctx.vault)
    except (ConnectionConfigError, VaultError) as e:
        return FleetStatus(error=str(e))
    if p.auth == "password" and not creds.password:
        return FleetStatus(error="No saved password. Connect once from Servers.")

    async def deny(host: str, fp: str, changed) -> bool:
        return False

    tmp = SSHClient(EventBus(), ctx.store.data.known_hosts, deny)
    try:
        await asyncio.wait_for(tmp.connect(creds), 25)
        return parse_light((await tmp.run(LIGHT_CMD, timeout=15)).stdout)
    except HostKeyRejected:
        return FleetStatus(error="Host key not trusted yet. Connect once from Servers.")
    except TakError as e:
        return FleetStatus(error=e.message)
    except asyncio.TimeoutError:
        return FleetStatus(error="Timed out.")
    finally:
        try:
            await tmp.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _connect_and_run_for_fleet(ctx: AppContext, run: FleetRun, cmd: str,
                                       sudo: bool, timeout: float) -> RunOutcome:
    """The `connect_and_run` hook we pass to FleetRunner.

    Opens a throwaway SSH connection (matching the existing `probe` pattern),
    runs `cmd`, and returns a `RunOutcome`. Never prompts the user — unknown
    host keys and missing passwords are surfaced as ERROR.
    """
    p = ctx.conns.get(run.profile_id)
    if p is None:
        return RunOutcome(error="Profile no longer exists.")
    try:
        creds = ctx.conns.credentials(p, ctx.vault)
    except (ConnectionConfigError, VaultError) as e:
        return RunOutcome(error=str(e))
    if p.auth == "password" and not creds.password:
        return RunOutcome(error="No saved password. Connect once from Servers.")

    async def deny(host: str, fp: str, changed) -> bool:
        return False

    tmp = SSHClient(EventBus(), ctx.store.data.known_hosts, deny)
    try:
        await asyncio.wait_for(tmp.connect(creds), min(25, timeout))
    except HostKeyRejected:
        return RunOutcome(error="Host key not trusted yet. Connect once from Servers.")
    except (TakError, asyncio.TimeoutError) as e:
        return RunOutcome(error=getattr(e, "message", None) or "Couldn't connect.")
    try:
        r = await tmp.run(cmd, timeout=timeout, sudo=sudo,
                            sudo_password=creds.password if sudo else None)
        return RunOutcome(stdout=r.stdout, stderr=r.stderr, exit_code=r.exit_code)
    except TakError as e:
        return RunOutcome(error=e.message)
    finally:
        try:
            await tmp.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _preflight_for_fleet(ctx: AppContext) -> callable:
    """Build a preflight callable that skips servers we know will fail at connect time."""
    def preflight(run: FleetRun) -> tuple[bool, str]:
        p = ctx.conns.get(run.profile_id)
        if p is None:
            return (False, "Profile no longer exists.")
        # We can't easily peek into the vault here without an await; the connect_and_run
        # hook already reports "no saved password" as an ERROR. So we accept every
        # profile and let the runner's connect path filter them out — but with a clear
        # error message rather than a silent hang.
        return (True, "")
    return preflight


class FleetView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.busy = False
        # Phase 14 Part 2: live fleet-run dialog state.
        self.runner: FleetRunner | None = None
        self.fleet_runs: list[FleetRun] = []
        self.fleet_dialog: ft.AlertDialog | None = None
        self.fleet_list = ft.ListView(expand=True, spacing=4)
        self.fleet_status = ft.Text("Idle.", size=12, opacity=0.7)
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("All servers", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.PLAY_CIRCLE, tooltip="Run on fleet…",
                                  width=44, height=44,
                                  on_click=lambda _: self.open_run_dialog()),
                    ft.IconButton(ft.Icons.REFRESH, tooltip="Check all", width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.refresh))], scroll=ft.ScrollMode.AUTO),
            ft.Text("A quick read-only check of every saved server. CPU is load average ÷ cores. "
                    "Use ▶ to run a command or snippet across all of them at once.",
                    size=11, opacity=0.6),
            self.progress, self.list], expand=True, spacing=8))
        self.render()

    async def refresh(self) -> None:
        if self.busy:
            return
        profiles = self.ctx.conns.list()
        if not profiles:
            self.render()
            return
        if not await self.ctx.ensure_unlocked():
            return
        self.busy = True
        self.progress.visible = True
        self.page.update()
        # Phase 15 (E2): configurable concurrency cap from settings.
        sem = asyncio.Semaphore(max(1, min(16, getattr(self.ctx.store.data, "fleet_concurrency", 4))))

        async def one(p: ConnectionProfile) -> None:
            async with sem:
                self.ctx.fleet[p.id] = await probe(self.ctx, p)
            self.render()

        await asyncio.gather(*(one(p) for p in profiles))
        self.busy = False
        self.progress.visible = False
        self.render()

    def render(self) -> None:
        thr = self.ctx._thresholds()
        tiles: list[ft.Control] = []
        for p in self.ctx.conns.list():
            s = self.ctx.fleet.get(p.id)
            h = health(s, thr) if s else "down"
            body: list[ft.Control] = [ft.Row([ft.Icon(ft.Icons.CIRCLE, size=12, color=_COLOR[h]),
                                              ft.Text(p.name, weight=ft.FontWeight.W_600, expand=True, no_wrap=True),
                                              ft.Text(_LABEL[h] if s else "not checked", size=11, opacity=0.8)]),
                                      ft.Text(p.target, size=11, opacity=0.6)]
            if s and s.ok:
                def bar(label, pct, lv):
                    return ft.Row([ft.Text(label, size=11, width=42), ft.ProgressBar(value=min(pct, 100) / 100, expand=True,
                                                                                    color=_COLOR[lv]), ft.Text(f"{pct:.0f}%", size=11, width=40)])
                from services.monitor import level
                body += [bar("CPU", s.cpu_pct, level(min(s.cpu_pct, 100), thr.cpu)), bar("RAM", s.mem_pct, level(s.mem_pct, thr.ram)),
                         bar("Disk", s.disk_pct, level(s.disk_pct, thr.disk)),
                         ft.Text(f"up {human_duration(s.uptime_s)}" + (f" · {s.failed} failed service(s)" if s.failed else "")
                                 + (" · reboot required" if s.reboot else "") + f" · {time_ago(s.taken_at)}", size=10, opacity=0.7)]
            elif s:
                body.append(ft.Text(s.error or "Unreachable.", size=11, color=ft.Colors.ERROR))
            tiles.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, ink=True,
                                      bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, on_click=lambda _, p=p: self.page.run_task(self.open, p),
                                      content=ft.Column(body, spacing=4)))
        self.list.controls = tiles or [ft.Container(padding=24, content=ft.Text("Add servers first.", opacity=0.7))]
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    async def open(self, p: ConnectionProfile) -> None:
        if await self.ctx.connect(p):
            self.ctx.navigate("overview")

    # ---------------------------------------------------------------- Phase 14 Part 2: fleet run
    def open_run_dialog(self) -> None:
        """Step 1: pick what to run — a one-off command, or a saved snippet."""
        profiles = self.ctx.conns.list()
        if not profiles:
            toast(self.page, "Add servers first.")
            return
        snippets = self.ctx.snippets.list_snippets("")     # global scope only for fleet runs
        col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        col.controls.append(ft.Text("Run a command across every saved server in parallel.",
                                     size=12, opacity=0.7))
        # Free-form command path
        cmd_field = ft.TextField(hint_text="uptime", label="Command (one line)",
                                  dense=True, expand=True, border_radius=8)
        sudo_sw = ft.Switch(label="sudo", value=False)
        col.controls.append(ft.Text("One-off command", size=13, weight=ft.FontWeight.W_600))
        col.controls.append(ft.Row([cmd_field, ft.Container(sudo_sw, padding=ft.Padding.only(top=8))], spacing=8))

        async def run_command(_=None) -> None:
            cmd = (cmd_field.value or "").strip()
            if not cmd:
                toast(self.page, "Enter a command first.", error=True)
                return
            self.page.pop_dialog()
            await self._start_fleet_run(cmd, bool(sudo_sw.value), [])

        col.controls.append(ft.FilledTonalButton("Run on all servers", icon=ft.Icons.PLAY_ARROW,
                                                  on_click=lambda _: self.page.run_task(run_command)))
        # Snippet path
        if snippets:
            col.controls.append(ft.Divider(height=1))
            col.controls.append(ft.Text("Or pick a saved snippet (global scope only)",
                                         size=13, weight=ft.FontWeight.W_600))
            for sn in snippets[:20]:
                col.controls.append(ft.Row([
                    ft.Icon(ft.Icons.BOLT, size=18, opacity=0.7),
                    ft.Column([ft.Text(sn.name, no_wrap=True, weight=ft.FontWeight.W_500),
                                ft.Text(sn.command.splitlines()[0][:60] if sn.command else "",
                                         size=11, opacity=0.6, no_wrap=True)],
                               spacing=0, expand=True),
                    ft.IconButton(ft.Icons.PLAY_ARROW, tooltip="Run on fleet", width=44, height=44,
                                  on_click=lambda _, s=sn: self.page.run_task(self._run_snippet_on_fleet, s)),
                ], spacing=4))
        col.controls.append(ft.Divider(height=1))
        col.controls.append(ft.Text("Every server runs the same command. Risky commands still need "
                                     "approval; you'll get one prompt covering all of them.", size=11, opacity=0.7))
        dlg = ft.AlertDialog(
            title=ft.Text("Run on fleet"), modal=True,
            content=ft.Container(width=440, height=min(420, (page.height or 600) - 200), content=col),
            actions=[ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog())])
        self.page.show_dialog(dlg)

    async def _run_snippet_on_fleet(self, sn) -> None:
        """Substitute variables once (same answer for every server), then start the run."""
        names = S.find_variables(sn.command)
        values: dict = {}
        if names:
            from ui.pages.snippets import _ask_variables
            values = await _ask_variables(self.ctx, sn, names)
            if values is None:
                return
        try:
            rendered = S.render(sn.command, values)
        except KeyError as e:
            toast(self.page, f"Missing value for {e.args[0]}.", error=True)
            return
        # Close the picker dialog before starting.
        try:
            self.page.pop_dialog()
        except Exception:  # noqa: BLE001
            pass
        await self._start_fleet_run(rendered, bool(sn.sudo), [])

    async def _start_fleet_run(self, command: str, sudo: bool, profiles_override: list) -> None:
        """Single approval gate for the whole fleet, then spin up the runner + live dialog."""
        # The risk classifier decides the approval flavour: dangerous → typed,
        # destructive/writes → confirm, safe → no prompt.
        risk = classify(command)
        if not await self._gate_fleet(command, risk):
            return
        profiles = profiles_override or self.ctx.conns.list()
        if not profiles:
            toast(self.page, "No servers to run on.")
            return
        # Pre-build the per-server FleetRun objects so the dialog renders immediately.
        self.fleet_runs = [
            FleetRun(profile_id=p.id, profile_name=p.name, target=p.target, command=command)
            for p in profiles
        ]
        for r in self.fleet_runs:
            rk = classify(command)
            r.risk_level = rk.level
            r.risk_explanation = rk.explanation
        # The runner: bounded concurrency, per-run timeout, no per-server gate (we
        # already approved above), no preflight (the connect_and_run hook reports
        # unreachable hosts as ERROR with a clear message).
        self.runner = FleetRunner(
            connect_and_run=lambda r, c, s, t: _connect_and_run_for_fleet(self.ctx, r, c, s, t),
            preflight=_preflight_for_fleet(self.ctx),
            gate=None,
            concurrency=max(1, min(16, getattr(self.ctx.store.data, "fleet_concurrency", 4))),
            per_run_timeout=90.0,
        )
        self._show_live_dialog(command, sudo)
        # Log every server we're about to touch — even if it fails — so the
        # activity page reflects the fleet action.
        a = self.ctx.active
        for p in profiles:
            self.ctx.activity.add(p.id, p.name, command[:2000], None, "fleet", risk.level)
        # Run on a background task so the UI keeps refreshing.
        self.page.run_task(self._drive_fleet_run, command, sudo, profiles)

    async def _drive_fleet_run(self, command: str, sudo: bool, profiles: list) -> None:
        # Run the fleet on a background task so we can refresh the dialog while
        # it's in flight. The runner mutates `self.fleet_runs` in place as each
        # task progresses, so polling that list gives us a live view.
        run_task = asyncio.ensure_future(self.runner.run(command, profiles, sudo=sudo))
        # Tick the dialog ~5x/second until the runner finishes.
        while not run_task.done():
            self._render_fleet_rows()
            self.fleet_status.value = self._live_summary()
            try:
                self.page.update()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(asyncio.shield(run_task), 0.2)
            except asyncio.TimeoutError:
                continue        # not done yet — keep ticking
            except asyncio.CancelledError:
                # The user closed the dialog or the app; let the runner finish.
                break
        # Pick up the result (may raise if the runner itself crashed).
        try:
            result = await run_task
        except Exception as e:  # noqa: BLE001
            toast(self.page, f"Fleet run crashed: {e}", error=True)
            return
        # Persist to fleet_history.jsonl.
        try:
            append_fleet_run(data_dir() / "fleet_history.jsonl", result, now=time.time())
        except Exception:  # noqa: BLE001
            pass
        # Final UI refresh with the authoritative result.
        self.fleet_runs = result.runs
        self.fleet_status.value = result.toast()
        self._render_fleet_rows()
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _live_summary(self) -> str:
        """Cheap in-flight summary: '2 done · 1 running · 2 pending'."""
        if not self.fleet_runs:
            return "Starting…"
        done = sum(1 for r in self.fleet_runs if r.state == RunState.DONE)
        failed = sum(1 for r in self.fleet_runs if r.state in AGG_HARD_FAIL)
        active = sum(1 for r in self.fleet_runs
                     if r.state in (RunState.RUNNING, RunState.CONNECTING))
        pending = sum(1 for r in self.fleet_runs if r.state == RunState.PENDING)
        skipped = sum(1 for r in self.fleet_runs if r.state == RunState.SKIPPED)
        parts = []
        if done: parts.append(f"{done} done")
        if failed: parts.append(f"{failed} failed")
        if active: parts.append(f"{active} running")
        if pending: parts.append(f"{pending} queued")
        if skipped: parts.append(f"{skipped} skipped")
        return " · ".join(parts) or "Starting…"

    async def _gate_fleet(self, command: str, risk) -> bool:
        """One approval prompt covers the whole fleet. Dangerous → typed; others → confirm."""
        shown = command if len(command) < 400 else command[:400] + "…"
        body = (f"Run on every saved server in parallel?\n\n```\n{shown}\n```\n\n"
                f"Risk: {risk.level}. {risk.explanation}")
        if risk.level == "dangerous":
            return await self.ctx.ask_typed("Dangerous fleet action", body, "RUN ALL")
        return await self.ctx.ask_yes_no("Run on fleet?", body, "Run on all")

    # ---- live dialog ----
    def _show_live_dialog(self, command: str, sudo: bool) -> None:
        self.fleet_status.value = "Starting…"
        self._render_fleet_rows()
        dlg = ft.AlertDialog(
            title=ft.Row([ft.Text("Fleet run", expand=True, weight=ft.FontWeight.W_600),
                          ft.Text(f"sudo" if sudo else "", size=11, opacity=0.7)]),
            modal=True,
            content=ft.Container(width=480, height=min(420, (page.height or 600) - 200), content=ft.Column([
                ft.Text(f"$ {command[:120]}", font_family="monospace", size=11, opacity=0.7,
                         no_wrap=True, selectable=True),
                self.fleet_status,
                ft.Container(self.fleet_list, expand=True),
            ], spacing=8, expand=True)),
            actions=[
                ft.TextButton("Stop all", on_click=lambda _: self._stop_all()),
                ft.TextButton("Close", on_click=lambda _: self._close_fleet_dialog()),
            ])
        self.fleet_dialog = dlg
        self.page.show_dialog(dlg)

    def _stop_all(self) -> None:
        if self.runner is not None:
            self.runner.cancel()
            toast(self.page, "Stopping every running task…")
            self.fleet_status.value = "Stopping…"
            self.page.update()

    def _close_fleet_dialog(self) -> None:
        try:
            self.page.pop_dialog()
        except Exception:  # noqa: BLE001
            pass
        self.fleet_dialog = None

    def _render_fleet_rows(self) -> None:
        """Rebuild the per-server rows in the live dialog. Called from the runner's tick."""
        rows: list[ft.Control] = []
        for r in order_for_display(self.fleet_runs):
            color = _STATE_COLOR.get(r.state, ft.Colors.ON_SURFACE_VARIANT)
            # Build the row body: name + target on the first line, summary + tail on the next.
            sub: list[ft.Control] = [ft.Text(r.summary(), size=11, color=color)]
            if r.state == RunState.DONE and r.stdout_tail:
                # Show the last line of stdout (e.g. `uptime` output).
                last = r.stdout_tail.strip().splitlines()[-1][:100] if r.stdout_tail.strip() else ""
                if last:
                    sub.append(ft.Text(last, size=10, font_family="monospace", opacity=0.7,
                                       no_wrap=True, selectable=True))
            elif r.state in AGG_HARD_FAIL and r.error:
                sub.append(ft.Text(r.error[:120], size=10, color=ft.Colors.ERROR, opacity=0.9,
                                   no_wrap=True, selectable=True))
            elif r.state == RunState.FAILED and r.stderr_tail:
                last = r.stderr_tail.strip().splitlines()[-1][:100] if r.stderr_tail.strip() else ""
                if last:
                    sub.append(ft.Text(last, size=10, font_family="monospace", opacity=0.7,
                                       no_wrap=True, selectable=True))
            rows.append(ft.Row([
                ft.Icon(_state_icon(r.state), size=18, color=color),
                ft.Column([
                    ft.Row([ft.Text(r.profile_name, weight=ft.FontWeight.W_500, no_wrap=True, expand=True),
                            ft.Text(r.target, size=10, opacity=0.6, no_wrap=True)]),
                    *sub,
                ], spacing=2, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
        self.fleet_list.controls = rows or [ft.Text("Waiting for first task…", opacity=0.7)]


def _state_icon(state: RunState) -> str:
    return {
        RunState.DONE: ft.Icons.CHECK_CIRCLE,
        RunState.FAILED: ft.Icons.ERROR_OUTLINE,
        RunState.ERROR: ft.Icons.CLOUD_OFF,
        RunState.SKIPPED: ft.Icons.SKIP_NEXT,
        RunState.CANCELLED: ft.Icons.CANCEL,
        RunState.PENDING: ft.Icons.SCHEDULE,
        RunState.CONNECTING: ft.Icons.SYNC,
        RunState.RUNNING: ft.Icons.TERMINAL,
    }.get(state, ft.Icons.CIRCLE)


def fleet_page(ctx: AppContext) -> ft.Control:
    v = FleetView(ctx)
    if not ctx.fleet:
        ctx.page.run_task(v.refresh)
    return v.control
