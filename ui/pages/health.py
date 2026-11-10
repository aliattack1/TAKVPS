"""Server health score with a plain-English list of what hurts it and a tap-through to fix each item."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import health as H
from services import packages as P
from services.i18n import translate
from .. import theme
from ..components.common import toast
from ..components.gauge import RingGauge
from ..context import AppContext


class HealthView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        _t = self._t
        self.gauge = RingGauge(_t("Health"), size=140)
        self.rating = ft.Text("", size=18, weight=ft.FontWeight.W_600)
        self.note = ft.Text("", size=11, opacity=0.7)
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.Column(spacing=8)
        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
            ft.Row([ft.Text(_t("Health score"), size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip=_t("Recheck"), width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.load))]),
            self.progress, ft.Row([self.gauge.control], alignment=ft.MainAxisAlignment.CENTER),
            ft.Row([self.rating], alignment=ft.MainAxisAlignment.CENTER), self.note, self.list])

    async def load(self) -> None:
        ctx = self.ctx
        _t = self._t
        if ctx.ssh.status.value != "connected":
            self.note.value = _t("Not connected.")
            self.page.update()
            return
        self.progress.visible = True
        self.note.value = _t("Checking disk, memory, load, services…")
        self.page.update()
        try:
            h = H.parse_health((await ctx.ssh.run(H.HEALTH_CMD, timeout=25)).stdout)
            pm = P.detect(ctx.sysinfo.package_manager) if ctx.sysinfo else None
            if pm:
                self.note.value = _t("Checking for updates (can take a moment)…")
                self.page.update()
                ups = P.parse_upgradable(pm, (await ctx.ssh.run(P.script_upgradable(pm), timeout=120)).stdout)
                h.security = sum(1 for u in ups if u.security)
                h.updates = len(ups) - h.security
                h.reboot = h.reboot or bool(P.parse_reboot((await ctx.ssh.run(P.REBOOT_CMD, timeout=15)).stdout))
        except TakError as e:
            toast(self.page, e.message, error=True)
            self.progress.visible = False
            self.page.update()
            return
        score, issues = H.compute(h)
        lvl = "ok" if score >= 90 else "warn" if score >= 60 else "crit"
        self.gauge.set(score, lvl, _t("out of 100"))
        self.rating.value = H.rating(score)
        self.note.value = (_t("Nothing is hurting this server's score. 🎉") if not issues else
                           _t("Tap an item to fix it. The score is a rough guide computed on your phone from live data."))
        self.list.controls = [ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE, ink=True, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            on_click=lambda _, k=i.fix: ctx.navigate(k),
            content=ft.Row([ft.Container(ft.Text(f"−{i.penalty}", size=12, weight=ft.FontWeight.W_700, color=ft.Colors.WHITE),
                                         bgcolor=ft.Colors.RED if i.penalty >= 12 else ft.Colors.AMBER_700,
                                         padding=ft.Padding.symmetric(horizontal=8, vertical=4), border_radius=6),
                            ft.Column([ft.Text(i.title, weight=ft.FontWeight.W_600), ft.Text(i.detail, size=11, opacity=0.7)],
                                      spacing=0, expand=True), ft.Icon(ft.Icons.CHEVRON_RIGHT)],
                           vertical_alignment=ft.CrossAxisAlignment.CENTER)) for i in issues]
        self.progress.visible = False
        self.page.update()


def health_page(ctx: AppContext) -> ft.Control:
    v = HealthView(ctx)
    ctx.page.run_task(v.load)
    return v.control
