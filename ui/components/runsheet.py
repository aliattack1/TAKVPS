"""Live command runner dialog: status, per-step progress, streaming output, stop, rollback."""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Awaitable, Callable, Optional

import flet as ft

from services.runner import WRONG_SUDO, RunReport, Runner, Step, strip_ansi
from .common import toast

MAX_SHOWN = 30_000


async def run_in_sheet(ctx, title: str, steps: list[Step], meta: dict,
                       rollback: Optional[Callable[[], Awaitable]] = None,
                       hints: Optional[Callable[[str], Optional[str]]] = None) -> Optional[RunReport]:
    """Run `steps` over SSH, streaming output into a dialog. `meta`: kind/id/name/command for history."""
    page = ctx.page
    if ctx.ssh.status.value != "connected":
        toast(page, "Not connected.", error=True)
        return None
    sudo_pw = None
    if any(s.sudo for s in steps):
        ok, sudo_pw = await ctx.get_sudo()
        if not ok:
            return None
        if ctx.sysinfo and ctx.sysinfo.sudo == "root":
            steps = [replace(s, sudo=False) for s in steps]          # root doesn't need (or have) sudo

    out = ft.Text("", font_family="monospace", size=11, selectable=True)
    out_list = ft.ListView([out], expand=True, auto_scroll=True)
    ring = ft.ProgressRing(width=18, height=18, stroke_width=2)
    status = ft.Text("Running…", weight=ft.FontWeight.W_600)
    hint = ft.Text("", size=12, color=ft.Colors.AMBER, visible=False)
    labels = [ft.Text(f"○ {s.name}", size=12) for s in steps]
    step_col = ft.Column(labels, spacing=2, visible=len(steps) > 1)
    stop_btn = ft.OutlinedButton("Stop", icon=ft.Icons.STOP)
    close_btn = ft.TextButton("Close", disabled=True)
    copy_btn = ft.TextButton("Copy output", icon=ft.Icons.COPY)
    rb_btn = ft.FilledButton("Roll back", icon=ft.Icons.UNDO, visible=False, bgcolor=ft.Colors.ERROR)
    state = {"dirty": False, "scheduled": False}

    def redraw() -> None:
        txt = strip_ansi(runner.output)
        out.value = txt if len(txt) <= MAX_SHOWN else "…\n" + txt[-MAX_SHOWN:]
        state["dirty"] = False
        page.update()

    async def flush() -> None:
        await asyncio.sleep(0.12)
        state["scheduled"] = False
        if state["dirty"]:
            redraw()

    def on_output(_text: str) -> None:
        state["dirty"] = True
        if not state["scheduled"]:
            state["scheduled"] = True
            page.run_task(flush)

    def on_step(i: int, st: Step, stat: str) -> None:
        labels[i].value = {"running": "▶ ", "done": "✓ ", "failed": "✗ "}[stat] + st.name
        page.update()

    runner = Runner(ctx.ssh, on_output, on_step)
    task = asyncio.ensure_future(runner.run(steps, sudo_pw))
    stop_btn.on_click = lambda _: (task.cancel(), setattr(status, "value", "Stopping…"), page.update())
    close_btn.on_click = lambda _: page.pop_dialog()
    copy_btn.on_click = lambda _: page.run_task(ctx.terminal.copy_text, strip_ansi(runner.output))
    page.show_dialog(ft.AlertDialog(
        modal=True, title=ft.Row([ring, ft.Text(title, no_wrap=True, expand=True)]),
        content=ft.Container(width=min(380, (page.width or 400) - 32), height=420, content=ft.Column(
            [status, hint, step_col, ft.Container(out_list, expand=True, padding=8, border_radius=8,
                                            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST)], expand=True)),
        actions=[stop_btn, copy_btn, rb_btn, close_btn]))

    t0 = time.monotonic()
    try:
        report = await task
    except asyncio.CancelledError:
        report = runner.report
    secs = time.monotonic() - t0

    if WRONG_SUDO.search(runner.output):
        ctx.forget_sudo()                        # wrong/refused sudo password: ask again next time
    ring.visible = stop_btn.visible = False
    close_btn.disabled = False
    if report.cancelled:
        status.value, status.color = "Stopped", ft.Colors.AMBER
    elif report.ok:
        status.value, status.color = "Succeeded ✓", ft.Colors.GREEN
    else:
        status.value, status.color = f"Failed (exit {report.last_exit})", ft.Colors.ERROR
        if rollback is not None:
            rb_btn.visible = True

            async def do_rb(_=None):
                page.pop_dialog()
                await rollback()
            rb_btn.on_click = lambda _: page.run_task(do_rb)
    if hints and (h := hints(runner.output)):
        hint.value, hint.visible = h, True
    redraw()

    a = ctx.active
    ctx.snippet_runs.add(meta.get("kind", "snippet"), meta.get("id", ""), meta.get("name", title),
                         a.id if a else "", report.last_exit, secs, strip_ansi(runner.output))
    ctx.activity.add(a.id if a else "", a.name if a else "", meta.get("command", title)[:500],
                     report.last_exit, meta.get("kind", "snippet"), meta.get("risk", "safe"))
    return report
