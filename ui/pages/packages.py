"""Package manager UI + Update Center (a UI over the server's own apt/dnf/yum/pacman/apk/zypper)."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import packages as P
from services.risk import classify
from services.runner import Step
from .. import theme
from ..components.common import toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext

MAX_RENDER = 300
MODES = [("updates", "Updates"), ("installed", "Installed"), ("search", "Search")]


class PackagesView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        raw = ctx.sysinfo.package_manager if ctx.sysinfo else ""
        self.pm = P.detect(raw)
        self.mode = "updates"
        self.upgrades: list[P.Upgrade] | None = None
        self.reboot: bool | None = None
        self.installed: list[P.Pkg] | None = None
        self.results: list[P.Pkg] | None = None
        self.query = ft.TextField(hint_text="Filter / search", dense=True, expand=True, prefix_icon=ft.Icons.SEARCH,
                                  on_change=lambda _: self.render(), on_submit=lambda _: self.page.run_task(self._submit))
        self.search_btn = ft.FilledButton("Search", on_click=lambda _: self.page.run_task(self.do_search))
        self.seg = ft.SegmentedButton(selected=["updates"], on_change=self._on_mode,
                                      segments=[ft.Segment(value=k, label=ft.Text(v)) for k, v in MODES])
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Packages", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.Text(self.pm or "", size=12, opacity=0.6),
                    ft.IconButton(ft.Icons.REFRESH, tooltip="Reload lists", width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.reload))]),
            self.seg, ft.Row([self.query, self.search_btn], visible=True), self.progress, self.list],
            expand=True, spacing=8))
        self._busy = False
        self.render()

    # ---- mode / loading ----
    def _on_mode(self, e) -> None:
        self.mode = next(iter(e.control.selected))
        self.query.value = ""
        self.search_btn.visible = self.mode == "search"
        self.page.run_task(self.reload, False)

    async def _submit(self) -> None:
        if self.mode == "search":
            await self.do_search()

    async def reload(self, force: bool = True) -> None:
        if self.mode == "updates" and (force or self.upgrades is None):
            await self.load_updates()
        elif self.mode == "installed" and (force or self.installed is None):
            await self.load_installed()
        else:
            self.render()

    def _ready(self) -> bool:
        return self.pm is not None and self.ctx.ssh.status.value == "connected"

    async def _run(self, cmd: str, timeout: float = 90) -> str:
        self._busy = True
        self.progress.visible = True
        self.page.update()
        try:
            return (await self.ctx.ssh.run(cmd, timeout=timeout)).stdout
        finally:
            self._busy = False
            self.progress.visible = False

    async def load_updates(self) -> None:
        if not self._ready():
            self.render()
            return
        try:
            self.upgrades = P.parse_upgradable(self.pm, await self._run(P.script_upgradable(self.pm), 150))
            self.reboot = P.parse_reboot(await self._run(P.REBOOT_CMD, 15))
        except TakError as e:
            toast(self.page, e.message, error=True)
        self.render()

    async def load_installed(self) -> None:
        if not self._ready():
            self.render()
            return
        try:
            self.installed = P.parse_installed(self.pm, await self._run(P.cmd_installed(self.pm), 60))
        except TakError as e:
            toast(self.page, e.message, error=True)
        self.render()

    async def do_search(self) -> None:
        if not self._ready():
            return
        try:
            cmd = P.cmd_search(self.pm, self.query.value or "")
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        try:
            self.results = P.parse_search(self.pm, await self._run(cmd, 60))
        except TakError as e:
            toast(self.page, e.message, error=True)
        self.render()

    # ---- operations ----
    async def operate(self, title: str, cmd: str, force: bool = False) -> None:
        if not await self.ctx.gate_risk(f"{title}?", [cmd], force_confirm=force):
            return
        rep = await run_in_sheet(self.ctx, title, [Step(title, cmd, sudo=True)],
                                 {"kind": "package", "name": title, "command": cmd, "risk": classify(cmd).level},
                                 hints=P.friendly_hint)
        if rep is not None and not rep.cancelled:
            self.installed = None
            await self.load_updates()

    async def changelog(self, name: str) -> None:
        cmd = P.cmd_changelog(self.pm, name)
        if not cmd:
            return
        try:
            out = (await self._run(cmd, 45)).strip()
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"{name} · changelog"), scrollable=True,
            content=ft.Container(width=380, content=ft.Text(out or "No changelog available.",
                                                            font_family="monospace", size=11, selectable=True)),
            actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))

    async def reboot_now(self) -> None:
        if not await self.ctx.gate_risk("Reboot the server?", ["reboot"]):
            return
        try:
            await self.ctx.run_privileged("reboot", 15)
        except TakError:
            pass                                            # the connection drops as the server goes down
        toast(self.page, "Reboot command sent. Reconnect in a minute or two.")

    # ---- rendering ----
    def render(self) -> None:
        self.progress.visible = self._busy
        self.search_btn.visible = self.mode == "search"
        if self.pm is None:
            msg = ("Not connected." if self.ctx.ssh.status.value != "connected" else
                   "Couldn't detect a supported package manager (apt, dnf, yum, pacman, apk or zypper).")
            self.list.controls = [ft.Container(padding=24, content=ft.Text(msg, opacity=0.7))]
        elif self.mode == "updates":
            self.list.controls = self._updates_view()
        elif self.mode == "installed":
            self.list.controls = self._installed_view()
        else:
            self.list.controls = self._search_view()
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _updates_view(self) -> list[ft.Control]:
        ups = self.upgrades
        if ups is None:
            return [ft.Container(padding=24, content=ft.Text("Checking for updates…", opacity=0.7))]
        sec = [u for u in ups if u.security]
        rest = [u for u in ups if not u.security]
        split = P.supports_security_split(self.pm)
        out: list[ft.Control] = []
        summary = f"{len(ups)} update(s) available" + (f" · {len(sec)} security" if split else "")
        buttons: list[ft.Control] = [ft.TextButton("Refresh index", icon=ft.Icons.SYNC, on_click=lambda _: self.page.run_task(
            self.operate, "Refresh package index", P.cmd_refresh(self.pm)))]
        if ups:
            buttons.append(ft.FilledButton("Update all", icon=ft.Icons.SYSTEM_UPDATE, on_click=lambda _: self.page.run_task(
                self.operate, "Update all packages", P.cmd_upgrade_all(self.pm), True)))
        if split and sec:
            buttons.append(ft.FilledTonalButton("Security only", icon=ft.Icons.SECURITY, on_click=lambda _: self.page.run_task(
                self.operate, "Install security updates", P.cmd_security(self.pm, [u.name for u in sec]), True)))
        out.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                                content=ft.Column([ft.Text(summary, weight=ft.FontWeight.W_600),
                                                   ft.Row(buttons, wrap=True, spacing=4)], spacing=6)))
        if self.reboot:
            out.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.ERROR_CONTAINER,
                                    content=ft.Row([ft.Icon(ft.Icons.RESTART_ALT), ft.Text("A reboot is required", expand=True),
                                                    ft.TextButton("Reboot…", on_click=lambda _: self.page.run_task(self.reboot_now))])))
        if not split and ups:
            out.append(ft.Text(f"{self.pm} doesn't label security updates, so they're listed together.", size=11, opacity=0.6))
        for title, group in (("Security updates", sec), ("Other updates", rest if split else ups)):
            if group:
                out.append(ft.Text(title, size=13, weight=ft.FontWeight.W_600, opacity=0.7))
                out += [self._update_tile(u) for u in group[:MAX_RENDER]]
        if not ups:
            out.append(ft.Container(padding=24, content=ft.Text("Everything is up to date. ✓", opacity=0.7)))
        return out

    def _update_tile(self, u: P.Upgrade) -> ft.Control:
        sub = f"{u.old} → {u.new}" if u.old else u.new
        row: list[ft.Control] = [ft.Column([ft.Text(u.name, weight=ft.FontWeight.W_600, no_wrap=True),
                                            ft.Text(sub, size=11, opacity=0.7, no_wrap=True)], spacing=0, expand=True)]
        if u.security:
            row.append(ft.Container(ft.Text("SECURITY", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.W_700),
                                    bgcolor=ft.Colors.RED, padding=ft.Padding.symmetric(horizontal=5, vertical=1), border_radius=4))
        if P.cmd_changelog(self.pm, u.name):
            row.append(ft.IconButton(ft.Icons.DESCRIPTION, tooltip="Changelog", width=44, height=44,
                                     on_click=lambda _, n=u.name: self.page.run_task(self.changelog, n)))
        row.append(ft.FilledTonalButton("Update", height=40, on_click=lambda _, n=u.name: self.page.run_task(
            self.operate, f"Update {n}", P.cmd_upgrade(self.pm, [n]))))
        return ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                            content=ft.Row(row, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def _installed_view(self) -> list[ft.Control]:
        if self.installed is None:
            self.page.run_task(self.load_installed) if not self._busy else None
            return [ft.Container(padding=24, content=ft.Text("Loading installed packages…", opacity=0.7))]
        q = (self.query.value or "").lower()
        pk = [p for p in self.installed if q in p.name.lower()]
        out: list[ft.Control] = [ft.Text(f"{len(pk)} of {len(self.installed)} packages", size=11, opacity=0.6)]
        for p in pk[:MAX_RENDER]:
            out.append(ft.Container(
                border_radius=theme.RADIUS, padding=ft.Padding.symmetric(horizontal=theme.SPACE, vertical=6),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, content=ft.Row([
                    ft.Column([ft.Text(p.name, weight=ft.FontWeight.W_500, no_wrap=True),
                               ft.Text(p.version, size=11, opacity=0.7, no_wrap=True)], spacing=0, expand=True),
                    ft.PopupMenuButton(items=[
                        ft.PopupMenuItem(content=ft.Text("Update"), icon=ft.Icons.UPDATE,
                                         on_click=lambda _, n=p.name: self.page.run_task(
                                             self.operate, f"Update {n}", P.cmd_upgrade(self.pm, [n]))),
                        ft.PopupMenuItem(content=ft.Text("Remove"), icon=ft.Icons.DELETE,
                                         on_click=lambda _, n=p.name: self.page.run_task(
                                             self.operate, f"Remove {n}", P.cmd_remove(self.pm, [n]), True))])],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        if len(pk) > MAX_RENDER:
            out.append(ft.Text(f"Showing {MAX_RENDER}. Use the filter to narrow down.", size=11, opacity=0.6))
        return out

    def _search_view(self) -> list[ft.Control]:
        if self.results is None:
            return [ft.Container(padding=24, content=ft.Text("Type a name and tap Search.", opacity=0.7))]
        out: list[ft.Control] = []
        for p in self.results:
            if not P.valid_pkg(p.name):
                continue
            out.append(ft.Container(
                border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Row([ft.Column([ft.Text(p.name, weight=ft.FontWeight.W_600, no_wrap=True),
                                           ft.Text(p.desc or p.version, size=11, opacity=0.7, max_lines=2)], spacing=0, expand=True),
                                ft.FilledTonalButton("Install", height=40, on_click=lambda _, n=p.name: self.page.run_task(
                                    self.operate, f"Install {n}", P.cmd_install(self.pm, [n])))],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        return out or [ft.Container(padding=24, content=ft.Text("No packages found.", opacity=0.7))]


def packages_page(ctx: AppContext) -> ft.Control:
    v = PackagesView(ctx)
    ctx.page.run_task(v.load_updates)
    return v.control
