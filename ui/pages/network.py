"""Network & ports: listening ports, connections, public IP, ping/traceroute/DNS."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import network as NW
from services.runner import Step
from .. import theme
from ..components.common import toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext

TABS = [("listen", "Listening"), ("conns", "Connections"), ("tools", "Tools")]


class NetworkView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.mode = "listen"
        self.ports: list[NW.ListenPort] | None = None
        self.conns: list | None = None
        self.sudo_names = False
        self.host = ft.TextField(label="Host or IP", hint_text="example.com", expand=True)
        self.seg = ft.SegmentedButton(selected=["listen"], on_change=self._on_mode,
                                      segments=[ft.Segment(value=k, label=ft.Text(v)) for k, v in TABS])
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Network", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, width=44, height=44, tooltip="Refresh",
                                  on_click=lambda _: self.page.run_task(self.load))], scroll=ft.ScrollMode.AUTO),
            self.seg, self.progress, self.list], expand=True, spacing=8))
        self.render()

    def _on_mode(self, e) -> None:
        self.mode = next(iter(e.control.selected))
        self.page.run_task(self.load)

    async def load(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.render()
            return
        self.progress.visible = True
        self.page.update()
        try:
            if self.mode == "listen":
                r = await (self.ctx.run_privileged(NW.LISTEN_CMD) if self.sudo_names else self.ctx.ssh.run(NW.LISTEN_CMD, timeout=20))
                self.ports = NW.parse_listen(r.stdout) if r else self.ports
            elif self.mode == "conns":
                r = await (self.ctx.run_privileged(NW.CONNS_CMD) if self.sudo_names else self.ctx.ssh.run(NW.CONNS_CMD, timeout=20))
                self.conns = NW.parse_conns(r.stdout) if r else self.conns
        except TakError as e:
            toast(self.page, e.message, error=True)
        self.progress.visible = False
        self.render()

    def _card(self, controls: list) -> ft.Control:
        return ft.Container(padding=theme.SPACE, border_radius=theme.RADIUS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                            content=ft.Column(controls, spacing=6))

    def _sudo_btn(self) -> ft.Control:
        return ft.TextButton("Show process names (sudo)", icon=ft.Icons.ADMIN_PANEL_SETTINGS, visible=not self.sudo_names,
                             on_click=lambda _: self._enable_sudo())

    def _enable_sudo(self) -> None:
        self.sudo_names = True
        self.page.run_task(self.load)

    def render(self) -> None:
        out: list[ft.Control] = []
        if self.ctx.ssh.status.value != "connected":
            out = [ft.Container(padding=24, content=ft.Text("Not connected.", opacity=0.7))]
        elif self.mode == "listen":
            out.append(self._sudo_btn())
            if self.ports is None:
                out.append(ft.Text("Loading…", opacity=0.7))
            for p in self.ports or []:
                out.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                    content=ft.Row([
                        ft.Column([ft.Text(f"{p.port}/{p.proto}", weight=ft.FontWeight.W_700, size=16),
                                   ft.Text(f"{p.addr}" + (f" · {p.process} (PID {p.pid})" if p.process else ""), size=11, opacity=0.7)],
                                  spacing=0, expand=True),
                        ft.Container(ft.Text("PUBLIC" if p.public else "local", size=10, color=ft.Colors.WHITE if p.public else None,
                                             weight=ft.FontWeight.W_700),
                                     bgcolor=ft.Colors.AMBER_700 if p.public else ft.Colors.SECONDARY_CONTAINER,
                                     padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=4)],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        elif self.mode == "conns":
            out.append(self._sudo_btn())
            cs = self.conns or []
            out.append(self._card([ft.Text(f"{len(cs)} established connection(s)", weight=ft.FontWeight.W_600)]
                                  + [ft.Row([ft.Text(ip, expand=True, font_family="monospace"), ft.Text(f"× {n}")])
                                     for ip, n in NW.top_remotes(cs)]))
            for h, port, proc in cs[:80]:
                out.append(ft.Text(f"{h}:{port}" + (f"  ← {proc}" if proc else ""), size=11, font_family="monospace", opacity=0.8))
        else:
            out += [self._card([
                self.host,
                ft.Row([ft.FilledTonalButton("Ping", on_click=lambda _: self.page.run_task(self.tool, "ping")),
                        ft.FilledTonalButton("Traceroute", on_click=lambda _: self.page.run_task(self.tool, "trace")),
                        ft.FilledTonalButton("DNS lookup", on_click=lambda _: self.page.run_task(self.tool, "dns"))], wrap=True, spacing=4)]),
                self._card([ft.Text("Public IP address", weight=ft.FontWeight.W_600),
                            ft.Text("The server asks api.ipify.org (or ifconfig.me) for its own address. That service sees the "
                                    "server's IP; nothing from your phone is sent.", size=11, opacity=0.7),
                            ft.FilledTonalButton("Look it up", on_click=lambda _: self.page.run_task(self.public_ip))])]
        self.list.controls = out
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    async def tool(self, kind: str) -> None:
        try:
            cmd = {"ping": NW.ping_command, "trace": NW.traceroute_command, "dns": NW.dns_command}[kind](self.host.value or "")
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        name = {"ping": "Ping", "trace": "Traceroute", "dns": "DNS lookup"}[kind] + f" {self.host.value.strip()}"
        await run_in_sheet(self.ctx, name, [Step(name, cmd)], {"kind": "network", "name": name, "command": cmd, "risk": "safe"})

    async def public_ip(self) -> None:
        try:
            r = await self.ctx.ssh.run(NW.PUBLIC_IP_CMD, timeout=20)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        ip = r.stdout.strip()
        self.page.show_dialog(ft.AlertDialog(title=ft.Text("Public IP"), content=ft.Text(ip or "Couldn't determine it (no outbound internet or curl).", selectable=True,
                                             size=18), actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))


def network_page(ctx: AppContext) -> ft.Control:
    v = NetworkView(ctx)
    ctx.page.run_task(v.load)
    return v.control
