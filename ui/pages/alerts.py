"""Alerts: threshold + service-down notifications while the app is running."""
from __future__ import annotations

import flet as ft

from core.fmt import time_ago
from .. import theme
from ..context import AppContext


def alerts_page(ctx: AppContext) -> ft.Control:
    s, page = ctx.store.data, ctx.page

    def on_enabled(e):
        s.alerts_enabled = e.control.value
        ctx.store.save()
        ctx.sync_alerts()

    def on_interval(e):
        s.alert_interval = int(e.control.value)
        ctx.store.save()

    def on_services(e):
        s.alert_services = [x.strip() for x in (e.control.value or "").split(",") if x.strip()][:20]
        ctx.store.save()

    hist = [ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                         content=ft.Row([ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.AMBER_700),
                                         ft.Column([ft.Text(a.title, weight=ft.FontWeight.W_600), ft.Text(a.detail, size=11, opacity=0.8),
                                                    ft.Text(time_ago(a.ts), size=10, opacity=0.6)], spacing=1, expand=True)]))
            for a in ctx.alert_history] or [ft.Text("No alerts yet.", opacity=0.7)]
    return ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
        ft.Text("Alerts", size=22, weight=ft.FontWeight.W_600),
        ft.Switch(label="Alert me about problems on the connected server", value=s.alerts_enabled, on_change=on_enabled),
        ft.Dropdown(label="Check every", value=str(s.alert_interval), on_select=on_interval,
                    options=[ft.DropdownOption(key=str(k), text=f"{k} seconds" if k < 60 else f"{k // 60} min") for k in (30, 60, 300)]),
        ft.TextField(label="Services to watch (comma separated)", value=", ".join(s.alert_services), on_change=on_services,
                     hint_text="nginx, ssh, docker"),
        ft.Text("Alerts fire at the CRITICAL thresholds set in Settings → Monitoring (CPU, RAM, disk), after two checks in a row, "
                "then at most every 30 minutes per problem. A watched service that isn't active also alerts.", size=12, opacity=0.7),
        ft.Container(border_radius=8, padding=10, bgcolor=ft.Colors.SECONDARY_CONTAINER, content=ft.Text(
            "Alerts only work while this app is running (Android may pause it in the background). This is not 24/7 monitoring, "
            "and nothing is installed on your server. They appear inside the app; Android system notifications need an extra "
            "plugin that isn't included yet.", size=12)),
        ft.FilledTonalButton("Send a test alert", icon=ft.Icons.NOTIFICATIONS, on_click=lambda _: ctx.notify("Test alert", "This is how alerts look.")),
        ft.Text("History", size=14, weight=ft.FontWeight.W_600), *hist])
