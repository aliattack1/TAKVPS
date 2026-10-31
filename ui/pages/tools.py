"""Tools hub: one tap to any manager. Later phases add tiles here."""
from __future__ import annotations

import flet as ft

from .. import theme
from ..context import AppContext

I = ft.Icons
TOOLS = [
    ("snippets", "Commands", I.BOLT, "Saved commands, deploys, packs"),
    ("services", "Services", I.SETTINGS_SUGGEST, "systemd services"),
    ("cron", "Cron", I.SCHEDULE, "Scheduled jobs & timers"),
    ("packages", "Packages", I.SYSTEM_UPDATE, "Install, remove, update"),
    ("docker", "Docker", I.VIEW_IN_AR, "Containers, images, compose"),
    ("nginx", "Nginx", I.LANGUAGE, "Sites, config test, HTTPS"),
    ("health", "Health score", I.HEALTH_AND_SAFETY, "0-100 with fixes"),
    ("diskusage", "Disk usage", I.PIE_CHART, "What uses space + cleanup"),
    ("databases", "Databases", I.STORAGE, "MySQL, Postgres, SQLite, Redis"),
    ("tunnels", "SSH tunnels", I.SWAP_HORIZ, "Forward server ports to your phone"),
    ("envedit", ".env editor", I.KEY, "Edit secrets safely"),
    ("fleet", "All servers", I.DASHBOARD_CUSTOMIZE, "Health of every saved server"),
    ("backups", "Backups", I.BACKUP, "Archive, download, schedule"),
    ("alerts", "Alerts", I.NOTIFICATIONS, "CPU, RAM, disk, services"),
    ("logs", "Logs", I.ARTICLE, "Journal & log files, live"),
    ("firewall", "Firewall", I.SHIELD, "ufw / firewalld, SSH-safe"),
    ("processes", "Processes", I.MEMORY, "Signals & priority"),
    ("network", "Network", I.LAN, "Ports, connections, ping"),
    ("users", "Users & keys", I.GROUP, "SSH keys, logins"),
    ("quick", "Quick actions", I.FLASH_ON, "Update, clean, reboot"),
    ("activity", "Activity", I.HISTORY, "What this app ran"),
    ("monitor", "Monitor", I.MONITOR_HEART, "Live server stats"),
    ("terminal", "Terminal", I.TERMINAL, "Interactive shell"),
    ("files", "Files", I.FOLDER, "Browse & transfer"),
    ("editor", "Editor", I.EDIT_NOTE, "Edit remote files"),
]


def tools_page(ctx: AppContext) -> ft.Control:
    tiles = [ft.Container(
        width=160, border_radius=theme.RADIUS, padding=theme.SPACE, ink=True,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, on_click=lambda _, k=key: ctx.navigate(k),
        content=ft.Column([ft.Icon(icon, size=28, color=ft.Colors.PRIMARY),
                           ft.Text(label, weight=ft.FontWeight.W_600),
                           ft.Text(desc, size=11, opacity=0.7)], spacing=4)) for key, label, icon, desc in TOOLS]
    return ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
        ft.Text("Tools", size=22, weight=ft.FontWeight.W_600),
        ft.Row(tiles, wrap=True, spacing=theme.SPACE, run_spacing=theme.SPACE),
        ft.Text("More tools arrive in later phases.", size=12, opacity=0.6)])
