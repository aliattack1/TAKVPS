"""Overview page: server identity, status, key facts, quick actions."""
from __future__ import annotations

import flet as ft

from core.fmt import human_duration, human_size
from core.ssh_client import Status
from services.i18n import translate
from .. import theme
from ..components.common import card, empty_state
from ..context import AppContext

_SUDO = {"root": "Root user", "passwordless": "Passwordless sudo",
         "password": "Sudo (needs password)", "none": "No sudo"}


def overview_page(ctx: AppContext) -> ft.Control:
    page = ctx.page
    lang = ctx.store.data.language
    _t = lambda s: translate(s, lang)  # noqa: E731
    if ctx.active is None:
        return ft.Column([
            empty_state(ft.Icons.DASHBOARD, _t("Not connected"), _t("Pick a server to see its overview.")),
            ft.Container(padding=theme.SPACE, alignment=ft.Alignment.CENTER,
                         content=ft.FilledButton(_t("Choose a server"), icon=ft.Icons.DNS,
                                                 on_click=lambda _: ctx.navigate("servers")))],
            expand=True)

    p, info = ctx.active, ctx.sysinfo
    connected = ctx.ssh.status is Status.CONNECTED
    chip_color = ft.Colors.GREEN if connected else ft.Colors.ERROR

    async def refresh(_):
        await ctx.refresh_sysinfo()

    async def disconnect(_):
        await ctx.disconnect()
        ctx.navigate("servers")

    header = card(ft.Row([
        ft.Container(width=6, height=48, border_radius=3, bgcolor=p.color),
        ft.Column([ft.Text(p.name, size=20, weight=ft.FontWeight.W_600),
                   ft.Text(f"{p.target}" + (f"  ·  {info.hostname}" if info and info.hostname else ""),
                           size=12, opacity=0.7)], spacing=2, expand=True),
        ft.Row([ft.Icon(ft.Icons.CIRCLE, size=10, color=chip_color),
                ft.Text(_t("Online") if connected else _t("Offline (stale)"), size=12)], spacing=4)],
        vertical_alignment=ft.CrossAxisAlignment.CENTER))

    controls: list[ft.Control] = [header]
    if info and info.is_root:
        controls.append(ft.Container(
            padding=theme.SPACE, border_radius=theme.RADIUS, bgcolor=ft.Colors.ERROR_CONTAINER,
            content=ft.Row([ft.Icon(ft.Icons.WARNING_AMBER),
                            ft.Text(_t("Logged in as root — commands run with full privileges."),
                                    expand=True)])))

    if info:
        def stat(label: str, value: str) -> ft.Control:
            return ft.Container(width=156, content=card(ft.Column(
                [ft.Text(_t(label), size=12, opacity=0.7),
                 ft.Text(value or "—", size=15, weight=ft.FontWeight.W_600)], spacing=2)))
        used = max(info.mem_total_kb - info.mem_avail_kb, 0) * 1024
        ram = f"{human_size(used)} / {human_size(info.mem_total_kb * 1024)}" if info.mem_total_kb else "—"
        controls.append(ft.Row(wrap=True, spacing=theme.SPACE, run_spacing=theme.SPACE, controls=[
            stat("OS", info.distro), stat("Kernel", info.kernel), stat("Architecture", info.arch),
            stat("Uptime", human_duration(info.uptime_s)),
            stat("Load (1/5/15)", " / ".join(f"{x:.2f}" for x in info.load)),
            stat("Memory", ram), stat("Privileges", _SUDO[info.sudo]),
            stat("Package manager", info.package_manager), stat("systemd", _t("Yes") if info.has_systemd else _t("No"))]))
    elif connected:
        controls.append(ft.ProgressRing())

    from .quick import quick_row
    controls.append(ft.Text(_t("Quick actions"), size=14, weight=ft.FontWeight.W_600, opacity=0.7))
    controls.append(quick_row(ctx))
    controls.append(ft.Row(wrap=True, spacing=theme.SPACE, controls=[
        ft.FilledTonalButton(_t("Terminal"), icon=ft.Icons.TERMINAL, on_click=lambda _: ctx.navigate("terminal"),
                             disabled=not connected),
        ft.FilledTonalButton(_t("Files"), icon=ft.Icons.FOLDER, on_click=lambda _: ctx.navigate("files"),
                             disabled=not connected),
        ft.FilledTonalButton(_t("Health score"), icon=ft.Icons.HEALTH_AND_SAFETY, on_click=lambda _: ctx.navigate("health"), disabled=not connected),
        ft.FilledTonalButton(_t("Refresh"), icon=ft.Icons.REFRESH, on_click=refresh, disabled=not connected),
        ft.OutlinedButton(_t("Disconnect"), icon=ft.Icons.POWER_SETTINGS_NEW, on_click=disconnect,
                          disabled=not connected)]))
    return ft.ListView(controls, padding=theme.SPACE, spacing=theme.SPACE, expand=True)
