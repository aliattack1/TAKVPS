"""TAKVPS entry point."""
import time

import flet as ft

from core.events import CONNECTION_STATUS
from storage.settings import SettingsStore
from ui import theme
from ui.context import SYSINFO_EVENT, AppContext
from ui.pages.lock import show_lock
from ui.shell import Shell


async def _show_tour(ctx) -> None:
    """a 4-card intro shown on first launch."""
    page = ctx.page
    ctx.store.data.first_run_done = True
    ctx.store.save()

    cards = [
        (ft.Icons.DNS, "Connect a server",
         "Go to the Servers page and tap Add. Enter the host, port, username, and password or SSH key. "
         "Tap Test to check reachability, then Save."),
        (ft.Icons.FOLDER, "Browse files",
         "The Files page uses SFTP — navigate, upload, download, rename, and delete with the toolbar. "
         "Deleted items go to the Recycle Bin on the server; restore from there."),
        (ft.Icons.SMART_TOY, "Ask the AI",
         "The AI assistant (robot icon in the topbar) can suggest and run commands. "
         "Bring your own API key (OpenAI, Anthropic, Gemini, or local Ollama). "
         "Secrets are redacted before sending."),
        (ft.Icons.APPS, "Explore Tools",
         "The Tools page (in the bottom bar) is the hub for every manager: Docker, Nginx, Cron, "
         "Packages, Firewall, Databases, and more. Tap any tile to open it."),
    ]
    idx = [0]

    def render_card() -> ft.Control:
        icon, title, body = cards[idx[0]]
        return ft.Column([
            ft.Icon(icon, size=48, color=ft.Colors.PRIMARY),
            ft.Text(title, size=18, weight=ft.FontWeight.W_700),
            ft.Text(body, size=13, selectable=True, text_align=ft.TextAlign.CENTER),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12)

    content_col = ft.Container(width=360, padding=20, content=render_card())

    def next_card(_=None) -> None:
        if idx[0] < len(cards) - 1:
            idx[0] += 1
            content_col.content = render_card()
            _update_buttons()
            page.update()
        else:
            page.pop_dialog()

    def prev_card(_=None) -> None:
        if idx[0] > 0:
            idx[0] -= 1
            content_col.content = render_card()
            _update_buttons()
            page.update()

    def skip(_=None) -> None:
        page.pop_dialog()

    btn_prev = ft.TextButton("Back", on_click=prev_card)
    btn_next = ft.FilledButton("Next", on_click=next_card)
    btn_skip = ft.TextButton("Skip", on_click=skip)

    def _update_buttons() -> None:
        btn_prev.visible = idx[0] > 0
        btn_next.text = "Done" if idx[0] == len(cards) - 1 else "Next"

    _update_buttons()
    dlg = ft.AlertDialog(
        title=ft.Text(f"Welcome to TAKVPS ({idx[0] + 1}/{len(cards)})", size=16),
        modal=True,
        content=content_col,
        actions=[btn_skip, btn_prev, btn_next])
    page.show_dialog(dlg)


async def main(page: ft.Page) -> None:
    page.title = "TAKVPS"
    page.padding = 0
    # Set reasonable default window size on desktop.
    try:
        page.window.width = 1000
        page.window.height = 750
        page.window.min_width = 400
        page.window.min_height = 600
    except Exception:  # noqa: BLE001
        pass
    store = SettingsStore()
    s = store.data
    theme.apply(page, s.theme_mode, s.preset, s.accent)

    ctx = AppContext(page, store)
    shell = Shell(ctx)
    ctx.navigate, ctx.refresh_ui = shell.select_key, shell.refresh
    ctx.palette_pages = lambda: [(d.key, d.label) for d in shell.dests]
    ctx.bus.subscribe(CONNECTION_STATUS, shell.on_status)
    ctx.bus.subscribe(SYSINFO_EVENT, lambda _info: shell.refresh())

    st = {"locked": False, "hidden_at": None}

    async def start() -> None:
        last = ctx.conns.get(s.last_connection_id) if s.last_connection_id else None
        shell.select_key("servers")
        if s.connect_on_launch and last and await ctx.connect(last):
            shell.select_key("overview")
        ctx.sync_alerts()

    def unlock_after_resume() -> None:
        st["locked"] = False
        shell.render()

    def lock_now(first: bool = False) -> None:
        if not ctx.applock.enabled or st["locked"]:
            return
        st["locked"] = True
        show_lock(ctx, (lambda: (st.__setitem__("locked", False), page.run_task(start))) if first else unlock_after_resume)

    def on_lifecycle(e) -> None:
        state = str(getattr(e, "state", getattr(e, "data", ""))).lower()
        if "paused" in state or "hidden" in state:
            st["hidden_at"] = time.time()
        elif "resumed" in state:
            h, st["hidden_at"] = st["hidden_at"], None
            if h and ctx.applock.enabled and time.time() - h >= ctx.applock.timeout:
                lock_now()

    try:                                              # re-lock when the app returns from the background
        page.on_app_lifecycle_state_change = on_lifecycle
    except Exception:  # noqa: BLE001
        pass

    if ctx.applock.enabled:
        lock_now(first=True)
    else:
        await start()
    # first-run tour.
    if not s.first_run_done:
        page.run_task(_show_tour, ctx)


if __name__ == "__main__":
    ft.run(main)
