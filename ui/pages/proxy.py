"""Reverse-proxy manager page (Caddy / Traefik)."""
from __future__ import annotations
import flet as ft
from core.errors import TakError
from services import reverse_proxy as RP
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext


class ProxyView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.info: RP.ProxyInfo | None = None
        self.busy = False
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Reverse Proxy", size=22, weight=ft.FontWeight.W_600, expand=True),
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
        try:
            r = await self.ctx.ssh.run(RP.detect_command(), timeout=10)
            self.info = RP.parse_detect(r.stdout)
        except TakError as e:
            self.list.controls = [ft.Text(e.message, color=ft.Colors.ERROR)]
            self.busy = False
            self.progress.visible = False
            self.page.update()
            return
        self.busy = False
        self.progress.visible = False
        self.render()

    def render(self) -> None:
        cols: list[ft.Control] = []
        info = self.info
        if not info or not info.kind:
            cols.append(ft.Column([
                ft.Icon(ft.Icons.LAN_OUTAGE, size=36, opacity=0.4),
                ft.Text("No reverse proxy detected.", weight=ft.FontWeight.W_600),
                ft.Text("This page manages Caddy or Traefik. Install one:\n"
                        "• Caddy: apt install caddy\n"
                        "• Traefik: download from traefik.io", size=12, opacity=0.7, selectable=True),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER))
        else:
            # Status card
            running = info.running
            cols.append(ft.Container(
                border_radius=theme.RADIUS, padding=theme.SPACE,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Row([
                    ft.Icon(ft.Icons.CIRCLE, size=12,
                            color=ft.Colors.GREEN if running else ft.Colors.OUTLINE),
                    ft.Text(f"{info.kind.title()} {info.version}".strip(),
                           weight=ft.FontWeight.W_600, expand=True),
                    ft.Text("Running" if running else "Stopped",
                           size=12, opacity=0.7),
                ])))
            # Templates (Caddy only for now)
            if info.kind == "caddy":
                cols.append(ft.Text("Quick configs", weight=ft.FontWeight.W_600, size=14))
                for label, tmpl_name in [("Static site", "static"),
                                          ("Reverse proxy", "proxy"),
                                          ("PHP-FPM site", "php")]:
                    cols.append(self._template_card(label, tmpl_name))
        self.list.controls = cols or [ft.Text("No data.", opacity=0.7)]
        self.page.update()

    def _template_card(self, label: str, tmpl_name: str) -> ft.Control:
        """A card that shows a template and lets the user view/copy it."""
        def show_template(_=None) -> None:
            domain_field = ft.TextField(label="Domain", value="example.com", dense=True)
            if tmpl_name == "static":
                doc_root = ft.TextField(label="Document root", value="/var/www/html", dense=True)
                extra = [doc_root]
                kwargs_fn = lambda: {"domain": domain_field.value or "", "doc_root": doc_root.value or ""}
            elif tmpl_name == "proxy":
                backend_host = ft.TextField(label="Backend host", value="127.0.0.1", dense=True)
                backend_port = ft.TextField(label="Backend port", value="8080", dense=True)
                extra = [backend_host, backend_port]
                kwargs_fn = lambda: {"domain": domain_field.value or "",
                                      "backend_host": backend_host.value or "",
                                      "backend_port": backend_port.value or ""}
            elif tmpl_name == "php":
                doc_root = ft.TextField(label="Document root", value="/var/www/html", dense=True)
                php_host = ft.TextField(label="PHP-FPM host", value="127.0.0.1", dense=True)
                php_port = ft.TextField(label="PHP-FPM port", value="9000", dense=True)
                extra = [doc_root, php_host, php_port]
                kwargs_fn = lambda: {"domain": domain_field.value or "",
                                      "doc_root": doc_root.value or "",
                                      "php_host": php_host.value or "",
                                      "php_port": php_port.value or ""}
            else:
                extra = []
                kwargs_fn = lambda: {"domain": domain_field.value or "", "doc_root": "/var/www"}

            def gen(_=None):
                kwargs = kwargs_fn()
                tmpl = RP.template_for("caddy", tmpl_name, **kwargs)
                result.value = tmpl
                self.page.update()

            result = ft.TextField(multiline=True, min_lines=6, max_lines=10,
                                   border_radius=8, text_size=10, font_family="monospace")
            for f in [domain_field] + extra:
                f.on_change = gen
            gen()
            self.page.show_dialog(ft.AlertDialog(
                title=ft.Text(f"Caddy: {label}"), modal=True, scrollable=True,
                content=ft.Container(width=400, content=ft.Column(
                    [domain_field] + extra + [result], tight=True, spacing=8)),
                actions=[
                    ft.TextButton("Copy", on_click=lambda _: self.page.run_task(self._copy, result)),
                    ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog()),
                ]))

        return ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE, ink=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, on_click=show_template,
            content=ft.Row([
                ft.Icon(ft.Icons.CODE, size=20, opacity=0.7),
                ft.Text(label, expand=True, weight=ft.FontWeight.W_500),
                ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20, opacity=0.5),
            ]))

    async def _copy(self, field: ft.TextField) -> None:
        text = field.value or ""
        try:
            clip = ft.Clipboard()
            self.page.services.append(clip)
            self.page.update()
            await clip.set(text)
            toast(self.page, "Copied to clipboard.")
        except Exception:
            toast(self.page, "Couldn't access the clipboard.", error=True)


def proxy_page(ctx: AppContext) -> ft.Control:
    v = ProxyView(ctx)
    ctx.page.run_task(v.load)
    return v.control
