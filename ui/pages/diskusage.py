"""Disk usage explorer (drill down by folder) + cleanup assistant."""
from __future__ import annotations

import shlex

import flet as ft

from core.errors import TakError
from core.fmt import human_size
from services import backups as B
from services import diskusage as DU
from services import packages as P
from services.i18n import translate
from services.runner import Step
from .. import theme
from ..components.common import toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext


class DiskView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        _t = self._t
        self.cwd = "/"
        self.items: list[DU.Cleanup] = []
        self.sizes: dict = {}
        self.checks: dict[str, ft.Checkbox] = {}
        self.progress = ft.ProgressBar(visible=False)
        self.fs = ft.Text("", size=12)
        self.path = ft.TextField(value="/", dense=True, expand=True, on_submit=lambda _: self.go(self.path.value or "/"))
        self.entries = ft.Column(spacing=4)
        self.clean = ft.Column(spacing=6)
        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
            ft.Text(_t("Disk usage"), size=22, weight=ft.FontWeight.W_600), self.progress, self.fs,
            ft.Row([ft.IconButton(ft.Icons.ARROW_UPWARD, tooltip=_t("Up"), width=44, height=44, on_click=lambda _: self.go(DU.parent(self.cwd))),
                    self.path, ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip=_t("Open in Files"), width=44, height=44, on_click=lambda _: self.to_files())]),
            self.entries, ft.Divider(),
            ft.Text(_t("Cleanup assistant"), size=16, weight=ft.FontWeight.W_600),
            ft.Text(_t("Measures what can be reclaimed, then cleans only what you tick, after a confirmation."), size=12, opacity=0.7),
            ft.FilledTonalButton(_t("Scan for cleanable space"), icon=ft.Icons.CLEANING_SERVICES, on_click=lambda _: self.page.run_task(self.scan)),
            self.clean])

    def to_files(self) -> None:
        self.ctx.files_state.path = self.cwd
        self.ctx.navigate("files")

    def go(self, path: str) -> None:
        self.cwd = path.strip() or "/"
        self.path.value = self.cwd
        self.page.run_task(self.load)

    async def load(self) -> None:
        ctx = self.ctx
        _t = self._t
        if ctx.ssh.status.value != "connected":
            self.entries.controls = [ft.Text(_t("Not connected."), opacity=0.7)]
            self.page.update()
            return
        self.progress.visible = True
        self.page.update()
        try:
            out = (await ctx.ssh.run(DU.du_command(self.cwd), timeout=120)).stdout
            df = (await ctx.ssh.run(f"df -Pk {shlex.quote(self.cwd)} | tail -n 1", timeout=15)).stdout.split()
        except (TakError, ValueError) as e:
            toast(self.page, getattr(e, "message", None) or str(e), error=True)
            self.progress.visible = False
            self.page.update()
            return
        total, es = DU.parse_du(out, self.cwd)
        if len(df) >= 6:
            self.fs.value = _t("Filesystem {fs} · {used} used of {total} ({pct}) · {free} free").format(
                fs=df[0], used=human_size(int(df[2]) * 1024),
                total=human_size(int(df[1]) * 1024), pct=df[4],
                free=human_size(int(df[3]) * 1024))
        rows = []
        for e in es[:40]:
            frac = e.kb / total if total else 0
            rows.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, ink=True, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                                     on_click=lambda _, p=e.path: self.go(p),
                                     content=ft.Column([ft.Row([ft.Icon(ft.Icons.FOLDER, color=ft.Colors.AMBER, size=18),
                                                                ft.Text(e.name, expand=True, no_wrap=True, weight=ft.FontWeight.W_500),
                                                                ft.Text(f"{human_size(e.kb * 1024)}  ·  {frac * 100:.0f}%", size=12)]),
                                                        ft.ProgressBar(value=min(frac, 1))], spacing=4)))
        self.entries.controls = [ft.Text(_t("{path} — {size}").format(path=self.cwd, size=human_size(total * 1024)),
                                     weight=ft.FontWeight.W_600)] + (
            rows or [ft.Text(_t("Nothing below this folder (or no access)."), opacity=0.7)])
        self.progress.visible = False
        self.page.update()

    # ---- cleanup assistant ----
    async def _docker_present(self) -> bool:
        try:
            return "docker" in (await self.ctx.ssh.run("command -v docker", timeout=10)).stdout
        except TakError:
            return False

    async def scan(self) -> None:
        ctx = self.ctx
        _t = self._t
        if ctx.ssh.status.value != "connected":
            toast(self.page, _t("Not connected."), error=True)
            return
        info = ctx.sysinfo
        pm = P.detect(info.package_manager) if info else None
        self.items = DU.items_for(pm or "", await self._docker_present(), bool(info and info.has_systemd))
        script = "sh -c " + shlex.quote(DU.measure_script(self.items))
        self.progress.visible = True
        self.page.update()
        try:
            r = await ctx.run_privileged(script, 90)                  # root sees every folder; asks for sudo once
            if r is None:
                r = await ctx.ssh.run(script, timeout=90)
        except TakError as e:
            toast(self.page, e.message, error=True)
            self.progress.visible = False
            self.page.update()
            return
        self.sizes = DU.parse_measure(r.stdout, self.items)
        self.progress.visible = False
        self.draw_cleanup()

    def draw_cleanup(self) -> None:
        _t = self._t
        self.checks = {}
        rows: list[ft.Control] = []
        for it in self.items:
            v = self.sizes.get(it.key)
            size = _t("unknown") if v is None else (_t("{n} package(s)").format(n=v) if it.unit == "count" else human_size(v * 1024))
            cb = ft.Checkbox(value=False, disabled=v in (None, 0))
            self.checks[it.key] = cb
            rows.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Row([cb, ft.Column([ft.Row([ft.Text(it.title, weight=ft.FontWeight.W_600, expand=True),
                                                       ft.Text(size, size=12, weight=ft.FontWeight.W_600)]),
                                               ft.Text(("⚠ " if it.caution else "") + it.note, size=11, opacity=0.7)], spacing=2, expand=True)],
                                vertical_alignment=ft.CrossAxisAlignment.START)))
        if rows:
            rows.append(ft.FilledButton(_t("Clean selected…"), icon=ft.Icons.DELETE_SWEEP, on_click=lambda _: self.page.run_task(self.run_clean)))
        self.clean.controls = rows or [ft.Text(_t("Nothing to clean was found."), opacity=0.7)]
        self.page.update()

    async def run_clean(self) -> None:
        _t = self._t
        chosen = [it for it in self.items if self.checks.get(it.key) and self.checks[it.key].value]
        if not chosen:
            toast(self.page, _t("Tick at least one item."))
            return
        lines = "\n".join(f"• {it.title}: {it.clean}" for it in chosen)
        if any(it.key == "dvolumes" for it in chosen):
            if not await self.ctx.ask_typed(_t("Delete unused Docker volumes?"), _t("Volumes not attached to a container will be PERMANENTLY "
                                            "deleted, including any data in them.\n\n{lines}").format(lines=lines), _t("CLEAN")):
                return
        elif not await self.ctx.ask_yes_no(_t("Clean up?"), lines, _t("Clean")):
            return
        if not await self.ctx.safe_confirm(_t("Cleanup"), lines):
            return
        free0 = await self._free()
        rep = await run_in_sheet(self.ctx, _t("Cleanup"), [Step(it.title, it.clean, sudo=True) for it in chosen],
                                 {"kind": "cleanup", "name": "cleanup", "command": "; ".join(it.clean for it in chosen), "risk": "destructive"},
                                 hints=P.friendly_hint)
        if rep is not None:
            freed = max(0, await self._free() - free0)
            toast(self.page, _t("Freed about {size}.").format(size=human_size(freed * 1024)))
            await self.load()
            await self.scan()

    async def _free(self) -> int:
        try:
            return B.parse_df_avail((await self.ctx.ssh.run("df -Pk / | tail -n 1", timeout=15)).stdout)
        except TakError:
            return 0


def diskusage_page(ctx: AppContext) -> ft.Control:
    v = DiskView(ctx)
    ctx.page.run_task(v.load)
    return v.control
