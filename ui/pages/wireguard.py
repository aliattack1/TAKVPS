"""WireGuard VPN manager page."""
from __future__ import annotations
import flet as ft
from core.errors import TakError
from core.fmt import human_size
from services import wireguard as WG
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext


class WireGuardView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.config: WG.WireGuardConfig | None = None
        self.status: dict = {}
        self.busy = False
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("WireGuard", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip="Refresh", width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.load))],
                   scroll=ft.ScrollMode.AUTO),
            self.progress, self.list], expand=True, spacing=8))

    async def load(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.list.controls = [ft.Text("Not connected.", opacity=0.7)]
            return
        self.busy = True
        self.progress.visible = True
        self.page.update()
        # Detect WireGuard
        try:
            r = await self.ctx.ssh.run(WG.detect_command(), timeout=10)
            if not r.stdout.strip():
                self.list.controls = [ft.Column([
                    ft.Icon(ft.Icons.VPN_LOCK, size=36, opacity=0.4),
                    ft.Text("WireGuard isn't installed on this server.", weight=ft.FontWeight.W_600),
                    ft.Text("Install it with: apt install wireguard wireguard-tools\n"
                            "or: dnf install wireguard-tools", size=12, opacity=0.7, selectable=True),
                    ft.FilledTonalButton("Install (apt)", icon=ft.Icons.DOWNLOAD,
                                          on_click=lambda _: self.page.run_task(self._install)),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)]
                self.busy = False
                self.progress.visible = False
                self.page.update()
                return
        except TakError as e:
            self.list.controls = [ft.Text(e.message, color=ft.Colors.ERROR)]
            self.busy = False
            self.progress.visible = False
            self.page.update()
            return
        # Read config
        try:
            r2 = await self.ctx.ssh.run(WG.read_config_command("wg0"), timeout=10)
            self.config = WG.parse_config(r2.stdout)
        except TakError:
            self.config = WG.WireGuardConfig()
        # Read status
        try:
            r3 = await self.ctx.ssh.run(WG.status_command("wg0"), timeout=10)
            self.status = WG.parse_status(r3.stdout)
        except TakError:
            self.status = {"running": False}
        self.busy = False
        self.progress.visible = False
        self.render()

    async def _install(self) -> None:
        if not await self.ctx.safe_confirm("Install WireGuard",
                                           "Run: apt update && apt install -y wireguard wireguard-tools"):
            return
        try:
            await self.ctx.ssh.run("apt update && apt install -y wireguard wireguard-tools", timeout=120, sudo=True,
                                   sudo_password=getattr(self.ctx, "_sudo_pw", None))
            toast(self.page, "WireGuard installed.")
        except TakError as e:
            toast(self.page, e.message, error=True)
        await self.load()

    def render(self) -> None:
        cols: list[ft.Control] = []
        # Status card
        running = self.status.get("running", False)
        cols.append(ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            content=ft.Row([
                ft.Icon(ft.Icons.CIRCLE, size=12,
                        color=ft.Colors.GREEN if running else ft.Colors.OUTLINE),
                ft.Text("Running" if running else "Stopped",
                       weight=ft.FontWeight.W_600, expand=True),
                ft.IconButton(ft.Icons.PLAY_ARROW, tooltip="Start (wg-quick up)", width=44, height=44,
                              on_click=lambda _: self.page.run_task(self._toggle, True)),
                ft.IconButton(ft.Icons.STOP, tooltip="Stop (wg-quick down)", width=44, height=44,
                              on_click=lambda _: self.page.run_task(self._toggle, False)),
            ])))
        # Interface info
        cfg = self.config
        if cfg and cfg.interface_private_key:
            cols.append(ft.Container(
                border_radius=theme.RADIUS, padding=theme.SPACE,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Column([
                    ft.Text("Interface", weight=ft.FontWeight.W_600),
                    ft.Text(f"Address: {cfg.interface_address or '—'}", size=12),
                    ft.Text(f"Listen port: {cfg.interface_listen_port or '—'}", size=12),
                    ft.Text(f"Private key: {cfg.interface_private_key[:8]}…{cfg.interface_private_key[-4:] if len(cfg.interface_private_key) > 12 else ''}", size=12,
                            font_family="monospace", selectable=True),
                ], spacing=4)))
        # Peers
        if cfg and cfg.peers:
            cols.append(ft.Text(f"Peers ({len(cfg.peers)})", weight=ft.FontWeight.W_600, size=14))
            for p in cfg.peers:
                cols.append(ft.Container(
                    border_radius=theme.RADIUS, padding=theme.SPACE,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                    content=ft.Column([
                        ft.Text(f"Key: {p.public_key[:8]}…{p.public_key[-4:] if len(p.public_key) > 12 else p.public_key}",
                                size=12, font_family="monospace", selectable=True),
                        ft.Text(f"Allowed IPs: {p.allowed_ips or '—'}", size=11, opacity=0.7),
                        ft.Text(f"Endpoint: {p.endpoint or '—'}", size=11, opacity=0.7),
                    ], spacing=2)))
        elif cfg:
            cols.append(ft.Text("No peers configured.", size=12, opacity=0.7))
        self.list.controls = cols or [ft.Text("No WireGuard config found.\nCreate /etc/wireguard/wg0.conf on the server.",
                                               opacity=0.7, selectable=True)]
        self.page.update()

    async def _toggle(self, up: bool) -> None:
        cmd = WG.up_command("wg0") if up else WG.down_command("wg0")
        action = "Start" if up else "Stop"
        if not await self.ctx.safe_confirm(f"{action} WireGuard", f"Run: {cmd}"):
            return
        try:
            await self.ctx.ssh.run(cmd, timeout=15, sudo=True,
                                   sudo_password=getattr(self.ctx, "_sudo_pw", None))
            toast(self.page, f"WireGuard {action.lower()}ed.")
        except TakError as e:
            toast(self.page, e.message, error=True)
        await self.load()


def wireguard_page(ctx: AppContext) -> ft.Control:
    v = WireGuardView(ctx)
    ctx.page.run_task(v.load)
    return v.control
