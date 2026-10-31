"""PIN lock screen. Full-page; replaces the app shell until the right PIN is entered."""
from __future__ import annotations

from typing import Callable

import flet as ft

from ..context import AppContext


def show_lock(ctx: AppContext, on_unlock: Callable[[], None]) -> None:
    page = ctx.page
    try:
        page.pop_dialog()                              # don't leave a dialog open over the lock screen
    except Exception:  # noqa: BLE001
        pass
    ctx.poller.set_active(False)
    page.controls.clear()
    page.navigation_bar = None
    page.drawer = None
    page.floating_action_button = None

    pin = ft.TextField(password=True, keyboard_type=ft.KeyboardType.NUMBER, max_length=8, autofocus=True,
                       text_align=ft.TextAlign.CENTER, width=220, hint_text="PIN")
    msg = ft.Text("", color=ft.Colors.ERROR, size=12)

    def attempt(_=None) -> None:
        ok, wait = ctx.applock.check(pin.value or "")
        if ok:
            page.controls.clear()
            on_unlock()
            return
        msg.value = f"Too many attempts. Try again in {wait} s." if wait else "Wrong PIN."
        pin.value = ""
        page.update()

    pin.on_submit = attempt
    page.add(ft.SafeArea(expand=True, content=ft.Column(
        [ft.Icon(ft.Icons.LOCK, size=56), ft.Text("TAKVPS is locked", size=20, weight=ft.FontWeight.W_600), pin, msg,
         ft.FilledButton("Unlock", icon=ft.Icons.LOCK_OPEN, on_click=attempt)],
        alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True, spacing=14)))
    page.update()
