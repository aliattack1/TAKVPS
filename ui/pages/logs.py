"""Logs viewer: pick a source, load or follow live, filter, highlight, pause, copy."""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import flet as ft

from core.errors import TakError
from services import logs as L
from services.i18n import translate
from services.runner import strip_ansi
from .. import theme
from ..components.common import toast
from ..context import AppContext

SHOWN = 500
MAX_LINES = 5000
_DENIED = re.compile(r"permission denied|insufficient permissions|not seeing messages|No journal files", re.I)
_COLOR = {"error": ft.Colors.RED_400, "warn": ft.Colors.AMBER_700}


class LogsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        self.sources = L.PRESETS
        self.src = L.PRESETS[0]
        self.lines: list[str] = []
        self.n = 300
        self.use_sudo = False
        self.task: Optional[asyncio.Task] = None
        self.paused = False
        self._partial, self._dirty, self._sched = "", False, False
        self.case = self.regex = False
        self.source_dd = ft.Dropdown(label=self._t("Log"), value=self.src.id, expand=True, on_select=self._on_source,
                                     options=[ft.DropdownOption(key=s.id, text=s.label) for s in self.sources])
        self.n_dd = ft.Dropdown(label=self._t("Lines"), value="300", width=110, on_select=lambda e: setattr(self, "n", int(e.control.value)),
                                options=[ft.DropdownOption(key=str(k), text=str(k)) for k in (100, 300, 1000, 3000)])
        self.search = ft.TextField(hint_text=self._t("Filter (text or regex)"), dense=True, expand=True,
                                   prefix_icon=ft.Icons.SEARCH, on_change=lambda _: self.render())
        self.case_btn = ft.IconButton(ft.Icons.TEXT_FIELDS, tooltip=self._t("Match case"), width=44, height=44,
                                      on_click=lambda _: self._flip("case"))
        self.re_btn = ft.IconButton(ft.Icons.CODE, tooltip=self._t("Regex"), width=44, height=44, on_click=lambda _: self._flip("regex"))
        self.follow_btn = ft.FilledTonalButton(self._t("Follow"), icon=ft.Icons.PODCASTS, on_click=lambda _: self.page.run_task(self.toggle_follow))
        self.pause_btn = ft.IconButton(ft.Icons.PAUSE, tooltip=self._t("Pause display"), width=44, height=44, visible=False,
                                       on_click=lambda _: self.toggle_pause())
        self.status = ft.Text("", size=11, opacity=0.7)
        self.out = ft.Text("", font_family="monospace", size=11, selectable=True)
        self.view = ft.ListView([self.out], expand=True, auto_scroll=True)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Text(self._t("Logs"), size=22, weight=ft.FontWeight.W_600),
            ft.Row([self.source_dd, self.n_dd], scroll=ft.ScrollMode.AUTO),
            ft.Row([self.search, self.case_btn, self.re_btn], scroll=ft.ScrollMode.AUTO),
            ft.Row([ft.FilledButton(self._t("Load"), icon=ft.Icons.REFRESH, on_click=lambda _: self.page.run_task(self.fetch)),
                    self.follow_btn, self.pause_btn,
                    ft.IconButton(ft.Icons.COPY, tooltip=self._t("Copy / export"), width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.export))], wrap=True, spacing=4),
            self.status, ft.Container(self.view, expand=True, padding=8, border_radius=8,
                                      bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST)], expand=True, spacing=8))
        ctx.page_hooks["logs"] = self._on_page

    # ---- lifecycle ----
    def _on_page(self, key: str) -> None:
        if key != "logs":
            self.stop()

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None
        self.follow_btn.text, self.pause_btn.visible = self._t("Follow"), False

    def _flip(self, which: str) -> None:
        setattr(self, which, not getattr(self, which))
        (self.case_btn if which == "case" else self.re_btn).selected = getattr(self, which)
        self.render()

    def _on_source(self, e) -> None:
        self.src = next(s for s in self.sources if s.id == e.control.value)
        self.stop()
        self.lines = []
        self.render()

    async def load_sources(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.status.value = self._t("Not connected.")
            self.page.update()
            return
        try:
            files = L.parse_log_files((await self.ctx.ssh.run(L.LIST_CMD, timeout=15)).stdout)
        except TakError:
            files = []
        self.sources = L.sources(files)
        self.source_dd.options = [ft.DropdownOption(key=s.id, text=s.label) for s in self.sources]
        self.page.update()
        await self.fetch()

    # ---- loading ----
    async def fetch(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            return
        self.stop()
        cmd = L.command(self.src, self.n)
        self.status.value = self._t("Loading…")
        self.page.update()
        try:
            r = await self.ctx.ssh.run(cmd, timeout=40)
            denied = _DENIED.search(r.stdout + r.stderr)
            if (denied or (not r.stdout.strip() and r.stderr.strip())) and not (self.ctx.sysinfo and self.ctx.sysinfo.sudo == "none"):
                r2 = await self.ctx.run_privileged(cmd, 40)
                if r2 is not None and r2.stdout.strip():
                    r, self.use_sudo = r2, True
        except TakError as e:
            toast(self.page, e.message, error=True)
            self.status.value = e.message
            self.page.update()
            return
        self.lines = [strip_ansi(l) for l in r.stdout.splitlines()][-MAX_LINES:]
        if not self.lines:
            self.lines = [(r.stderr.strip() or self._t("(no log lines)"))]
        self.render()

    async def toggle_follow(self) -> None:
        if self.task and not self.task.done():
            self.stop()
            self.render()
            return
        pw = None
        if self.use_sudo:
            ok, pw = await self.ctx.get_sudo()
            if not ok:
                return
        sudo = self.use_sudo and not (self.ctx.sysinfo and self.ctx.sysinfo.sudo == "root")
        self.paused = False
        self.task = asyncio.ensure_future(self.ctx.ssh.run_stream(self.src.follow, self._chunk, sudo=sudo, sudo_password=pw))
        self.follow_btn.text, self.pause_btn.visible = self._t("Stop following"), True
        self.render()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_btn.icon = ft.Icons.PLAY_ARROW if self.paused else ft.Icons.PAUSE
        self.render()

    def _chunk(self, text: str) -> None:
        data = self._partial + strip_ansi(text)
        parts = data.split("\n")
        self._partial = parts.pop()
        self.lines.extend(parts)
        del self.lines[:-MAX_LINES]
        self._dirty = True
        if not self.paused and not self._sched:
            self._sched = True
            self.page.run_task(self._flush)

    async def _flush(self) -> None:
        await asyncio.sleep(0.2)
        self._sched = False
        if self._dirty and not self.paused:
            self.render()

    # ---- rendering ----
    def render(self) -> None:
        try:
            vis = L.filter_lines(self.lines, self.search.value or "", self.case, self.regex)
            err = ""
        except ValueError as e:
            vis, err = self.lines, str(e)
        shown = vis[-SHOWN:]
        spans = []
        for ln in shown:
            spans.append(ft.TextSpan(ln + "\n", ft.TextStyle(color=_COLOR.get(L.severity(ln)))))
        self.out.spans = spans or [ft.TextSpan(self._t("(nothing to show)"))]
        self.status.value = err or (self._t("{n} of {m} lines").format(n=len(vis), m=len(self.lines))
                                    + (self._t(" · paused") if self.paused else "")
                                    + (self._t(" · following") if self.task and not self.task.done() else ""))
        self.status.color = ft.Colors.ERROR if err else None
        self._dirty = False
        self.page.update()

    async def export(self) -> None:
        try:
            vis = L.filter_lines(self.lines, self.search.value or "", self.case, self.regex)
        except ValueError:
            vis = self.lines
        await self.ctx.terminal.copy_text("\n".join(vis))


def logs_page(ctx: AppContext) -> ft.Control:
    v = LogsView(ctx)
    ctx.page.run_task(v.load_sources)
    return v.control
