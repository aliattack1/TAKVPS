"""Process manager: search, sort, signals, renice. Critical processes need a typed confirmation."""
from __future__ import annotations

import asyncio

import flet as ft

from core.errors import TakError
from core.fmt import human_duration
from services import processes as PR
from .. import theme
from ..components.common import toast
from ..context import AppContext

MAX_RENDER = 150
SORTS = [("cpu", "CPU"), ("mem", "Memory"), ("pid", "PID"), ("name", "Name")]


class ProcessesView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.procs: list[PR.Proc] = []
        self.sort = "cpu"
        self.msg = "Loading…"
        self.auto: asyncio.Task | None = None
        self.search = ft.TextField(hint_text="Search name, user or PID", dense=True, prefix_icon=ft.Icons.SEARCH,
                                   on_change=lambda _: self.render())
        self.seg = ft.SegmentedButton(selected=["cpu"], on_change=self._on_sort,
                                      segments=[ft.Segment(value=k, label=ft.Text(v)) for k, v in SORTS])
        self.auto_sw = ft.Switch(label="Auto-refresh (3 s)", value=False, on_change=self._on_auto)
        self.list = ft.ListView(expand=True, spacing=6)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Processes", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, width=44, height=44, tooltip="Refresh",
                                  on_click=lambda _: self.page.run_task(self.load))], scroll=ft.ScrollMode.AUTO),
            self.search, self.seg, self.auto_sw, self.list], expand=True, spacing=8))
        ctx.page_hooks["processes"] = lambda key: self._stop_auto() if key != "processes" else None
        self.render()

    def _on_sort(self, e) -> None:
        self.sort = next(iter(e.control.selected))
        self.render()

    def _on_auto(self, e) -> None:
        if e.control.value:
            self.auto = asyncio.ensure_future(self._auto_loop())
        else:
            self._stop_auto()

    def _stop_auto(self) -> None:
        if self.auto and not self.auto.done():
            self.auto.cancel()
        self.auto = None
        self.auto_sw.value = False

    async def _auto_loop(self) -> None:
        while True:
            await self.load()
            await asyncio.sleep(3)

    async def load(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.procs, self.msg = [], "Not connected."
        else:
            try:
                self.procs = PR.parse_ps((await self.ctx.ssh.run(PR.LIST_CMD, timeout=20)).stdout)
                self.msg = "" if self.procs else "Couldn't read the process list (this server's `ps` may not support the needed options)."
            except TakError as e:
                self.msg = e.message
        self.render()

    def render(self) -> None:
        vis = PR.sort_procs(PR.filter_procs(self.procs, self.search.value or ""), self.sort)
        tiles = [self._tile(p) for p in vis[:MAX_RENDER]]
        if len(vis) > MAX_RENDER:
            tiles.append(ft.Text(f"Showing {MAX_RENDER} of {len(vis)}. Use search to narrow down.", size=11, opacity=0.6))
        self.list.controls = tiles or [ft.Container(padding=24, content=ft.Text(self.msg or "No matches.", opacity=0.7))]
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _tile(self, p: PR.Proc) -> ft.Control:
        items = [ft.PopupMenuItem(content=ft.Text(f"{s} — {PR.SIGNAL_HELP[s]}"),
                                  on_click=lambda _, s=s: self.page.run_task(self.signal, p, s)) for s in PR.SIGNALS]
        items.append(ft.PopupMenuItem(content=ft.Text("Change priority (nice)…"), icon=ft.Icons.TUNE,
                                      on_click=lambda _: self.page.run_task(self.renice, p)))
        return ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            content=ft.Row([ft.Column([
                ft.Text(p.name, weight=ft.FontWeight.W_600, no_wrap=True),
                ft.Text(p.cmd, size=10, opacity=0.6, no_wrap=True, font_family="monospace"),
                ft.Text(f"PID {p.pid} · {p.user} · up {human_duration(p.age)} · nice {p.nice} · {p.stat}", size=10, opacity=0.7)],
                spacing=1, expand=True),
                ft.Column([ft.Text(f"{p.cpu:.1f}%", size=12, weight=ft.FontWeight.W_600), ft.Text(f"{p.mem:.1f}% mem", size=10)],
                          spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END),
                ft.PopupMenuButton(items=items)], vertical_alignment=ft.CrossAxisAlignment.CENTER))

    async def _run(self, cmd: str, what: str) -> None:
        if not await self.ctx.safe_confirm(what, cmd):
            return
        try:
            r = await self.ctx.ssh.run(cmd, timeout=20)
            if not r.ok and PR.permission_denied(r.stderr + r.stdout):
                if await self.ctx.ask_yes_no("Administrator rights needed", "This process belongs to another user. Retry with sudo?", "Retry"):
                    r = await self.ctx.run_privileged(cmd, 20)
                else:
                    return
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        if r is None:
            return
        toast(self.page, f"{what}: done" if r.ok else (r.stderr or r.stdout).strip() or f"{what} failed",
              error=not r.ok)
        await self.load()

    async def signal(self, p: PR.Proc, sig: str) -> None:
        try:
            cmd = PR.signal_command(p.pid, sig)
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        if PR.is_critical(p):
            if not await self.ctx.ask_typed("Critical process", f"“{p.name}” (PID {p.pid}) is essential: stopping it can break "
                                            "the server or cut off your SSH access.", "KILL"):
                return
        elif sig in ("TERM", "KILL", "STOP") and not await self.ctx.gate_risk(f"Send {sig} to {p.name} (PID {p.pid})?", [cmd]):
            return
        await self._run(cmd, f"{sig} → {p.name}")

    async def renice(self, p: PR.Proc) -> None:
        v = await self.ctx.ask_text("Priority", f"Nice value for {p.name} (-20 high … 19 low)", ok="Apply", value=p.nice)
        if v is None:
            return
        try:
            cmd = PR.renice_command(p.pid, int(v))
        except ValueError as e:
            toast(self.page, str(e) if "Nice" in str(e) or "PID" in str(e) else "Enter a whole number.", error=True)
            return
        await self._run(cmd, f"renice {p.name}")


def processes_page(ctx: AppContext) -> ft.Control:
    v = ProcessesView(ctx)
    ctx.page.run_task(v.load)
    return v.control
