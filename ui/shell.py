"""Responsive app shell.

Phone: bottom bar (3 pages + More) with a drawer; files and AI chat open as tall dialogs; a floating
       AI button on non-input pages.
Wide:  navigation rail; one docked side panel (Files or AI chat) can be open next to any page.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import flet as ft

from core.ssh_client import Status
from .components.common import ConnectionBanner
from .context import AppContext
from .components.palette import open_palette
from .pages.activity import activity_page
from .pages.alerts import alerts_page
from .pages.backups import backups_page
from .pages.chat import ChatController
from .pages.cron import cron_page
from .pages.databases import databases_page
from .pages.diskusage import diskusage_page
from .pages.envedit import envedit_page
from .pages.docker import docker_page
from .pages.connections import connections_page
from .pages.editor import EditorController
from .pages.files import FileBrowser, files_page
from .pages.firewall import firewall_page
from .pages.fleet import fleet_page
from .pages.health import health_page
from .pages.logs import logs_page
from .pages.network import network_page
from .pages.monitor import monitor_page
from .pages.nginx import nginx_page
from .pages.overview import overview_page
from .pages.packages import packages_page
from .pages.processes import processes_page
from .pages.quick import quick_page
from .pages.placeholder import placeholder
from .pages.services import services_page
from .pages.settings import settings_page
from .pages.snippets import open_snippet_editor, snippets_page
from .pages.terminal import TerminalController
from .pages.tools import tools_page
from .pages.tunnels import tunnels_page
from .pages.users import users_page
from .pages.wireguard import wireguard_page
from .pages.k8s import k8s_page
from .pages.proxy import proxy_page
from .theme import PHONE_MAX_WIDTH, RADIUS, SPACE

LIVE_KEYS = ("servers", "overview", "files", "snippets", "services", "cron", "packages", "docker", "nginx", "firewall", "processes", "network", "users", "activity", "fleet", "backups", "tunnels", "diskusage", "health", "databases")   # rebuilt on connection events
BOTTOM_SLOTS = 4
PANEL_WIDTH = 400
FAB_KEYS = ("overview", "monitor", "servers", "tools", "settings", "files", "terminal",
            "editor", "snippets", "services", "cron", "packages", "docker", "nginx",
            "logs", "firewall", "processes", "network", "users", "quick", "activity",
            "fleet", "backups", "alerts", "health", "diskusage", "databases",
            "tunnels", "envedit", "wireguard", "k8s", "proxy")
# pages that show a badge count (key → count function name on ctx)
BADGE_KEYS = {"alerts": "_alert_count", "activity": "_activity_count"}


@dataclass
class Dest:
    key: str
    label: str
    icon: str
    build: Callable[[], ft.Control]


class Shell:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        ctx.editor = EditorController(ctx)
        ctx.open_editor = ctx.editor.open_from_files
        ctx.terminal = TerminalController(ctx)
        ctx.chat = ChatController(ctx)
        ctx.open_chat = self.open_chat
        ctx.save_snippet = lambda cmd: open_snippet_editor(ctx, None, prefill=cmd)
        self.banner = ConnectionBanner(ctx.reconnect)
        self.body = ft.Container(expand=True)
        self.index = 0
        self.panel: Optional[str] = None            # None | "files" | "chat" (wide screens)
        self.dests = self._destinations()
        self._wide: bool | None = None
        self.page.on_resize = self._on_resize

    def _destinations(self) -> list[Dest]:
        c, I, P = self.ctx, ft.Icons, placeholder
        reg = {
            "overview": Dest("overview", "Overview", I.DASHBOARD, lambda: overview_page(c)),
            "monitor": Dest("monitor", "Monitor", I.MONITOR_HEART, lambda: monitor_page(c)),
            "files": Dest("files", "Files", I.FOLDER, lambda: files_page(c)),
            "editor": Dest("editor", "Editor", I.EDIT_NOTE, lambda: c.editor.build()),
            "snippets": Dest("snippets", "Commands", I.BOLT, lambda: snippets_page(c)),
            "terminal": Dest("terminal", "Terminal", I.TERMINAL, lambda: c.terminal.build()),
            "servers": Dest("servers", "Servers", I.DNS, lambda: connections_page(c)),
            "services": Dest("services", "Services", I.SETTINGS_SUGGEST, lambda: services_page(c)),
            "cron": Dest("cron", "Cron", I.SCHEDULE, lambda: cron_page(c)),
            "packages": Dest("packages", "Packages", I.SYSTEM_UPDATE, lambda: packages_page(c)),
            "docker": Dest("docker", "Docker", I.VIEW_IN_AR, lambda: docker_page(c)),
            "nginx": Dest("nginx", "Nginx", I.LANGUAGE, lambda: nginx_page(c)),
            "logs": Dest("logs", "Logs", I.ARTICLE, lambda: logs_page(c)),
            "firewall": Dest("firewall", "Firewall", I.SHIELD, lambda: firewall_page(c)),
            "processes": Dest("processes", "Processes", I.MEMORY, lambda: processes_page(c)),
            "network": Dest("network", "Network", I.LAN, lambda: network_page(c)),
            "users": Dest("users", "Users & keys", I.GROUP, lambda: users_page(c)),
            "quick": Dest("quick", "Quick actions", I.FLASH_ON, lambda: quick_page(c)),
            "activity": Dest("activity", "Activity", I.HISTORY, lambda: activity_page(c)),
            "fleet": Dest("fleet", "All servers", I.DASHBOARD_CUSTOMIZE, lambda: fleet_page(c)),
            "backups": Dest("backups", "Backups", I.BACKUP, lambda: backups_page(c)),
            "alerts": Dest("alerts", "Alerts", I.NOTIFICATIONS, lambda: alerts_page(c)),
            "health": Dest("health", "Health score", I.HEALTH_AND_SAFETY, lambda: health_page(c)),
            "diskusage": Dest("diskusage", "Disk usage", I.PIE_CHART, lambda: diskusage_page(c)),
            "databases": Dest("databases", "Databases", I.STORAGE, lambda: databases_page(c)),
            "tunnels": Dest("tunnels", "SSH tunnels", I.SWAP_HORIZ, lambda: tunnels_page(c)),
            "envedit": Dest("envedit", ".env editor", I.KEY, lambda: envedit_page(c)),
            "wireguard": Dest("wireguard", "WireGuard", I.VPN_LOCK, lambda: wireguard_page(c)),
            "k8s": Dest("k8s", "Kubernetes", I.CLOUD, lambda: k8s_page(c)),
            "proxy": Dest("proxy", "Reverse Proxy", I.LAN, lambda: proxy_page(c)),
            "tools": Dest("tools", "Tools", I.APPS, lambda: tools_page(c)),
            "settings": Dest("settings", "Settings", I.SETTINGS, lambda: settings_page(c)),
        }
        saved = list(c.store.data.nav_order)
        if "monitor" not in saved:                      # migrate older settings
            saved.insert(1 if saved else 0, "monitor")
        if "editor" not in saved:
            saved.insert(saved.index("files") + 1 if "files" in saved else len(saved), "editor")
        # make 'tools' reachable from the bottom bar on phones.
        # Previously it was at index 5+ (buried in the drawer), so the only way
        # to reach it was the search palette. Now BOTTOM_SLOTS=4 and we ensure
        # tools sits inside the first 4 slots.
        if "tools" not in saved:
            saved.insert(min(3, len(saved)), "tools")
        elif saved.index("tools") >= BOTTOM_SLOTS:
            # User has tools but it's outside the bottom-bar slots — move it up.
            saved.remove("tools")
            saved.insert(BOTTOM_SLOTS - 1, "tools")
        order = [k for k in saved if k in reg]
        order += [k for k in ("tools", "snippets", "services", "cron", "packages", "docker", "nginx", "logs", "firewall", "processes", "network", "users", "quick", "activity", "fleet", "backups", "alerts", "health", "diskusage", "databases", "tunnels", "envedit", "wireguard", "k8s", "proxy", "servers", "settings") if k not in order]
        order += [k for k in reg if k not in order]
        # Persist the migrated nav_order back so we don't redo this every render.
        if list(c.store.data.nav_order) != saved:
            c.store.data.nav_order = saved
            try:
                c.store.save()
            except Exception:  # noqa: BLE001
                pass
        return [reg[k] for k in order]

    @property
    def key(self) -> str:
        return self.dests[self.index].key

    # ---- navigation ----
    def _is_wide(self) -> bool:
        return (self.page.width or 400) >= PHONE_MAX_WIDTH

    def _on_resize(self, _=None) -> None:
        self.ctx.terminal.on_resize()
        if self._is_wide() != self._wide:
            self.render()

    def _close_drawer(self) -> None:
        for name in ("close_drawer", "pop_dialog"):
            fn = getattr(self.page, name, None)
            if fn:
                try:
                    fn()
                    return
                except Exception:  # noqa: BLE001
                    continue

    def _select(self, i: int) -> None:
        self.index = i
        self.render()

    def select_key(self, key: str) -> None:
        for i, d in enumerate(self.dests):
            if d.key == key:
                self._select(i)
                return

    def _sync_poller(self) -> None:
        """Poll only while the Monitor page is on screen; keep terminal key handling in sync."""
        self.ctx.poller.set_active(self.key == "monitor")
        if self.key != "monitor":
            self.ctx.monitor_view = None
        self.ctx.terminal.set_visible(self.key == "terminal")
        for fn in list(self.ctx.page_hooks.values()):
            fn(self.key)

    def refresh(self) -> None:
        if self.key in LIVE_KEYS:
            self.body.content = self.dests[self.index].build()
            self.page.update()

    def on_status(self, st) -> None:
        self.banner.set_status(st.value if isinstance(st, Status) else str(st))
        self.refresh()

    # ---- side panels / dialogs ----
    def _dialog_size(self) -> tuple[int, int]:
        return min((self.page.width or 400) - 32, 560), int((self.page.height or 700) * 0.82)

    def _toggle_files(self, _=None) -> None:
        if self._is_wide():
            self.panel = None if self.panel == "files" else "files"
            self.render()
        else:
            # On mobile: full-screen file browser dialog
            w = min((self.page.width or 400) - 16, 600)
            h = min((self.page.height or 700) - 80, 700)
            browser = FileBrowser(self.ctx, compact=True, on_close=lambda: self.page.pop_dialog())
            self.page.show_dialog(ft.AlertDialog(
                modal=True,
                content=ft.Container(width=w, height=h, content=browser.control)))

    def _toggle_chat(self, _=None) -> None:
        if self._is_wide() and self.panel == "chat":
            self.panel = None
            self.render()
        else:
            self.open_chat()

    def open_chat(self, prefill: Optional[str] = None) -> None:
        chat = self.ctx.chat
        if self._is_wide():
            if self.panel == "chat" and chat.view:
                if prefill:
                    chat.prefill(prefill)
                return
            chat.pending_prefill = prefill
            self.panel = "chat"
            self.render()
        else:
            # On mobile: open chat as a full-screen dialog so it fills the
            # available space even when the keyboard is open.
            chat.pending_prefill = prefill
            view = chat.build_view(compact=True, on_close=lambda: self.page.pop_dialog())
            w = min((self.page.width or 400) - 16, 600)
            h = min((self.page.height or 700) - 80, 700)
            self.page.show_dialog(ft.AlertDialog(
                modal=True,
                content=ft.Container(width=w, height=h, content=view)))

    def _close_panel(self) -> None:
        self.panel = None
        self.render()

    def _topbar(self) -> ft.Control | None:
        if self.key == "files":
            return None
        title = self.ctx.active.name if self.ctx.active else "TAKVPS"
        wide = self._is_wide()
        from .components.common import toolbar
        from .page_help import PAGE_HELP
        help_text = PAGE_HELP.get(self.key, "No help available for this page.")
        # On mobile: show fewer buttons in the topbar to avoid overflow.
        # The title text is shorter and buttons are smaller.
        if wide:
            return ft.Container(
                padding=ft.Padding.symmetric(horizontal=SPACE),
                content=toolbar(
                    ft.Text(title, size=16, weight=ft.FontWeight.W_600, no_wrap=True),
                    ft.IconButton(ft.Icons.SEARCH, tooltip="Search everything", width=44, height=44,
                                  on_click=lambda _: open_palette(self.ctx)),
                    ft.IconButton(ft.Icons.HELP_OUTLINE, tooltip="Help", width=44, height=44,
                                  on_click=lambda _: self._show_help(help_text)),
                    ft.IconButton(ft.Icons.SMART_TOY, tooltip="AI assistant", width=44, height=44,
                                  selected=self.panel == "chat", on_click=self._toggle_chat),
                    ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip="Files", width=44, height=44,
                                  selected=self.panel == "files", on_click=self._toggle_files)))
        else:
            # Mobile: compact topbar — just title + AI button (files accessible via Tools)
            return ft.Container(
                padding=ft.Padding.symmetric(horizontal=4),
                content=ft.Row([
                    ft.Text(title, size=14, weight=ft.FontWeight.W_600, expand=True, no_wrap=True),
                    ft.IconButton(ft.Icons.SEARCH, tooltip="Search", width=36, height=36,
                                  on_click=lambda _: open_palette(self.ctx)),
                    ft.IconButton(ft.Icons.HELP_OUTLINE, tooltip="Help", width=36, height=36,
                                  on_click=lambda _: self._show_help(help_text)),
                    ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip="Files", width=36, height=36,
                                  selected=self.panel == "files", on_click=self._toggle_files),
                ], spacing=0))

    def _show_help(self, text: str) -> None:
        """show a help dialog for the current page."""
        dlg = ft.AlertDialog(
            scrollable=True,
            title=ft.Text("Help"), modal=True,
            content=ft.Container(width=380, content=ft.Column([
                ft.Text(text, size=13, selectable=True),
            ], tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())])
        self.page.show_dialog(dlg)

    # ---- layout ----
    def render(self) -> None:
        p = self.page
        self._wide = self._is_wide()
        # wrap the page build in a try/except so a construction
        # error shows a friendly message instead of crashing the app.
        try:
            self.body.content = self.dests[self.index].build()
        except Exception as e:  # noqa: BLE001
            self.body.content = ft.Container(
                padding=40, expand=True,
                content=ft.Column([
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.ERROR),
                    ft.Text("This page failed to load.", size=16, weight=ft.FontWeight.W_600),
                    ft.Text(str(e)[:300], size=11, opacity=0.7, selectable=True,
                             font_family="monospace"),
                    ft.Text("Try navigating away and back. If the error persists, the page "
                             "may depend on a feature this server doesn't support.",
                             size=12, opacity=0.6),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8))
        p.controls.clear()
        p.navigation_bar = None
        p.drawer = None
        p.floating_action_button = None
        top = self._topbar()
        main_col = ft.Column([c for c in (top, self.banner, self.body) if c is not None],
                             expand=True, spacing=0)
        # Constrain the main content area so it doesn't stretch too wide
        # on large desktop screens (e.g. 2560px monitors). Max 900px for
        # readability; centered when the window is wider.
        main_wrapper = ft.Container(
            content=main_col, expand=True,
            alignment=ft.Alignment.CENTER)
        if self._wide:
            def rail_pick(e):
                self._select(int(e.control.selected_index))

            # Build a custom scrollable navigation sidebar instead of NavigationRail,
            # because NavigationRail doesn't scroll when there are many destinations.
            rail_items: list[ft.Control] = []
            for i, d in enumerate(self.dests):
                is_sel = i == self.index
                rail_items.append(ft.Container(
                    ink=True, on_click=lambda _, i=i: self._select(i),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                    bgcolor=ft.Colors.SECONDARY_CONTAINER if is_sel else None,
                    border_radius=RADIUS,
                    content=ft.Column([
                        ft.Icon(d.icon, size=22,
                                color=ft.Colors.PRIMARY if is_sel else ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(d.label, size=10, no_wrap=True,
                                color=ft.Colors.PRIMARY if is_sel else ft.Colors.ON_SURFACE_VARIANT),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
                    width=80))
            rail_list = ft.ListView(rail_items, spacing=2, padding=ft.Padding.symmetric(vertical=8),
                                     expand=True, auto_scroll=True)
            rail_col = ft.Container(
                content=rail_list,
                width=90, expand=True,
                bgcolor=ft.Colors.SURFACE)
            row: list[ft.Control] = [rail_col, ft.VerticalDivider(width=1), main_wrapper]
            if self.panel == "files" and self.key != "files":
                panel = FileBrowser(self.ctx, compact=True, on_close=self._close_panel)
                row += [ft.VerticalDivider(width=1), ft.Container(width=PANEL_WIDTH, content=panel.control)]
            elif self.panel == "chat":
                view = self.ctx.chat.build_view(compact=True, on_close=self._close_panel)
                row += [ft.VerticalDivider(width=1),
                        ft.Container(width=PANEL_WIDTH + 40, padding=8, content=view)]
            p.add(ft.SafeArea(expand=True, content=ft.Row(row, expand=True)))
        else:
            def drawer_pick(e):
                self._close_drawer()
                self._select(int(e.control.selected_index))

            def bar_pick(e):
                i = int(e.control.selected_index)
                if i < BOTTOM_SLOTS:
                    self._select(i)
                else:                               # "Settings" navigates to settings + opens drawer
                    # First navigate to settings page
                    self.select_key("settings")
                    # Then open the drawer so other pages are reachable too
                    try:
                        p.show_drawer()
                    except Exception:  # noqa: BLE001
                        pass

            p.navigation_bar = ft.NavigationBar(
                selected_index=min(self.index, BOTTOM_SLOTS),
                destinations=[ft.NavigationBarDestination(icon=d.icon, label=d.label)
                              for d in self.dests[:BOTTOM_SLOTS]] +
                             [ft.NavigationBarDestination(icon=ft.Icons.SETTINGS, label="Settings")],
                on_change=bar_pick)
            p.drawer = ft.NavigationDrawer(
                selected_index=self.index,
                controls=[ft.NavigationDrawerDestination(icon=d.icon, label=d.label) for d in self.dests],
                on_change=drawer_pick)
            if self.key in FAB_KEYS:
                p.floating_action_button = ft.FloatingActionButton(
                    icon=ft.Icons.SMART_TOY, tooltip="AI assistant", on_click=lambda _: self.open_chat())
            p.add(ft.SafeArea(expand=True, content=main_col))
        self._sync_poller()
        p.update()
