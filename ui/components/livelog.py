"""Live tail dialog: streams a command's output with pause, filter, copy and stop."""
from __future__ import annotations

import asyncio
from collections import deque

import flet as ft

from services.runner import strip_ansi
from .common import toast

MAX_LINES = 3000
SHOWN = 500


async def open_live_tail(ctx, title: str, cmd: str, sudo: bool = False) -> None:
    page = ctx.page
    if ctx.ssh.status.value != "connected":
        toast(page, "Not connected.", error=True)
        return
    pw = None
    if sudo and not (ctx.sysinfo and ctx.sysinfo.sudo == "root"):
        ok, pw = await ctx.get_sudo()
        if not ok:
            return
    use_sudo = sudo and not (ctx.sysinfo and ctx.sysinfo.sudo == "root")

    lines: deque = deque(maxlen=MAX_LINES)
    st = {"paused": False, "partial": "", "dirty": False, "sched": False, "filter": ""}
    text = ft.Text("", font_family="monospace", size=11, selectable=True)
    lst = ft.ListView([text], expand=True, auto_scroll=True)
    status = ft.Text("Following…", size=12)
    flt = ft.TextField(hint_text="Filter lines", dense=True, expand=True)
    pause = ft.IconButton(ft.Icons.PAUSE, tooltip="Pause", width=44, height=44)

    def render() -> None:
        f = st["filter"]
        vis = [l for l in lines if not f or f in l.lower()]
        text.value = "\n".join(vis[-SHOWN:])
        st["dirty"] = False
        page.update()

    async def flush() -> None:
        await asyncio.sleep(0.15)
        st["sched"] = False
        if st["dirty"] and not st["paused"]:
            render()

    def on_chunk(chunk: str) -> None:
        data = st["partial"] + strip_ansi(chunk)
        parts = data.split("\n")
        st["partial"] = parts.pop()
        lines.extend(parts)
        st["dirty"] = True
        if not st["paused"] and not st["sched"]:
            st["sched"] = True
            page.run_task(flush)

    def toggle_pause(_) -> None:
        st["paused"] = not st["paused"]
        pause.icon = ft.Icons.PLAY_ARROW if st["paused"] else ft.Icons.PAUSE
        pause.tooltip = "Resume" if st["paused"] else "Pause"
        status.value = "Paused (still collecting)" if st["paused"] else "Following…"
        if not st["paused"]:
            render()
        else:
            page.update()

    def on_filter(e) -> None:
        st["filter"] = (e.control.value or "").lower()
        render()

    pause.on_click, flt.on_change = toggle_pause, on_filter
    task = asyncio.ensure_future(ctx.ssh.run_stream(cmd, on_chunk, sudo=use_sudo, sudo_password=pw))

    def done(t: asyncio.Task) -> None:
        status.value = "Ended" if not t.cancelled() else "Stopped"
        page.update()
    task.add_done_callback(done)

    def close(_=None) -> None:
        if not task.done():
            task.cancel()
        page.pop_dialog()

    page.show_dialog(ft.AlertDialog(
        modal=True, title=ft.Text(title, no_wrap=True),
        content=ft.Container(width=380, height=440, content=ft.Column([
            ft.Row([flt, pause, ft.IconButton(ft.Icons.COPY, tooltip="Copy", width=44, height=44,
                                              on_click=lambda _: page.run_task(ctx.terminal.copy_text, "\n".join(lines)))]),
            status, ft.Container(lst, expand=True, padding=8, border_radius=8,
                                 bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST)], expand=True)),
        actions=[ft.TextButton("Close", on_click=close)]))
