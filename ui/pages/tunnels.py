"""SSH tunnels: forward a port on the server to a port on this phone (127.0.0.1 only)."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import tunnels as T
from .. import theme
from ..components.common import toast
from ..context import AppContext


class TunnelsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.name = ft.TextField(label="Name", hint_text="Web app")
        self.rhost = ft.TextField(label="Remote host (as the server sees it)", value="127.0.0.1")
        self.rport = ft.TextField(label="Remote port", keyboard_type=ft.KeyboardType.NUMBER, width=150, hint_text="8080")
        self.lport = ft.TextField(label="Local port (blank = auto)", keyboard_type=ft.KeyboardType.NUMBER, width=190)
        self.fav = ft.Switch(label="Save as favorite", value=False)
        self.active = ft.Column(spacing=6)
        self.favs = ft.Column(spacing=6)
        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
            ft.Text("SSH tunnels", size=22, weight=ft.FontWeight.W_600),
            ft.Text("Reach a service on the server (a web UI, a database, a model server) from this phone, through your SSH "
                    "connection. Ports are only opened on this phone's loopback (127.0.0.1) — never on the network.", size=12, opacity=0.7),
            self.name, self.rhost, ft.Row([self.rport, self.lport], wrap=True), self.fav,
            ft.FilledButton("Start tunnel", icon=ft.Icons.SWAP_HORIZ, on_click=lambda _: self.page.run_task(self.start_form)),
            ft.Text("Active", weight=ft.FontWeight.W_600), self.active,
            ft.Text("Favorites", weight=ft.FontWeight.W_600), self.favs,
            ft.Text("Tip: for an AI model on your server, start a tunnel to its port and use http://127.0.0.1:<local port>/v1 as the "
                    "base URL of an AI profile. Tunnels stop when the connection drops.", size=11, opacity=0.6)])
        self.render()

    @property
    def cid(self) -> str:
        return self.ctx.active.id if self.ctx.active else ""

    def _spec(self) -> T.TunnelSpec:
        def num(tf: ft.TextField, blank: int = 0) -> int:
            v = (tf.value or "").strip()
            return int(v) if v else blank
        try:
            return T.TunnelSpec((self.name.value or "").strip(), num(self.rport, -1), (self.rhost.value or "").strip() or "127.0.0.1",
                                num(self.lport, 0))
        except ValueError:
            raise ValueError("Ports must be whole numbers.")

    async def start_form(self) -> None:
        try:
            spec = self._spec()
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        if await self.start(spec) and self.fav.value and self.cid:
            self.ctx.tunnel_favs.add(self.cid, spec)
        self.render()

    async def start(self, spec: T.TunnelSpec) -> bool:
        if self.ctx.ssh.status.value != "connected":
            toast(self.page, "Not connected.", error=True)
            return False
        try:
            t = await self.ctx.tunnels.start(spec)
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return False
        except TakError as e:
            toast(self.page, e.message, error=True)
            return False
        self.ctx.log_action(f"ssh tunnel 127.0.0.1:{t.port} -> {spec.remote_host}:{spec.remote_port}", 0)
        toast(self.page, f"Tunnel open on {t.address}")
        self.render()
        return True

    def render(self) -> None:
        act = []
        for t in self.ctx.tunnels.active.values():
            act.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Column([ft.Row([ft.Icon(ft.Icons.CIRCLE, size=10, color=ft.Colors.GREEN),
                                           ft.Text(t.spec.label, weight=ft.FontWeight.W_600, expand=True, no_wrap=True)]),
                                   ft.Text(f"{t.address}  →  {t.spec.remote_host}:{t.spec.remote_port}", size=11,
                                           font_family="monospace", selectable=True),
                                   ft.Row([ft.FilledTonalButton("Open in browser", icon=ft.Icons.OPEN_IN_BROWSER, height=36,
                                                                on_click=lambda _, u=t.local_url: self.page.run_task(self.ctx.open_url, u)),
                                           ft.TextButton("Copy", on_click=lambda _, a=t.address: self.page.run_task(self.ctx.terminal.copy_text, a)),
                                           ft.TextButton("Stop", on_click=lambda _, i=t.spec.id: self._stop(i))],
                                          wrap=True, spacing=4)], spacing=4)))
        self.active.controls = act or [ft.Text("No active tunnels.", opacity=0.7)]
        favs = []
        for s in self.ctx.tunnel_favs.list(self.cid):
            favs.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Row([ft.Column([ft.Text(s.label, weight=ft.FontWeight.W_600, no_wrap=True),
                                           ft.Text(f"{s.remote_host}:{s.remote_port}" + (f" → local {s.local_port}" if s.local_port else ""),
                                                   size=11, opacity=0.7)], spacing=0, expand=True),
                                ft.FilledTonalButton("Start", height=36, disabled=s.id in self.ctx.tunnels.active,
                                                     on_click=lambda _, s=s: self.page.run_task(self.start, s)),
                                ft.IconButton(ft.Icons.DELETE, width=40, height=40, on_click=lambda _, s=s: self._unfav(s))],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        self.favs.controls = favs or [ft.Text("No favorites yet.", opacity=0.7)]
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _stop(self, sid: str) -> None:
        self.ctx.tunnels.stop(sid)
        self.render()

    def _unfav(self, s: T.TunnelSpec) -> None:
        self.ctx.tunnel_favs.remove(self.cid, s.id)
        self.render()


def tunnels_page(ctx: AppContext) -> ft.Control:
    return TunnelsView(ctx).control
