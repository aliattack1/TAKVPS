"""TAKVPS entry point."""
import time

import flet as ft

from core.events import CONNECTION_STATUS
from storage.settings import SettingsStore
from ui import theme
from ui.context import SYSINFO_EVENT, AppContext
from ui.pages.lock import show_lock
from ui.shell import Shell


async def main(page: ft.Page) -> None:
    page.title = "TAKVPS"
    page.padding = 0
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


if __name__ == "__main__":
    ft.run(main)
