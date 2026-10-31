"""Reusable widgets: toast, confirm dialog, connection banner, empty state, toolbar."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import flet as ft

from ..theme import MIN_TAP, RADIUS, SPACE


def toast(page: ft.Page, text: str, error: bool = False) -> None:
    page.show_dialog(ft.SnackBar(
        ft.Text(text), bgcolor=ft.Colors.ERROR_CONTAINER if error else None))


def toolbar(*controls: ft.Control, expand_title: bool = True) -> ft.Row:
    """A horizontally-scrollable row for page topbars and action bars.

    Phase 14 fix: on narrow phones, action-button rows used to overflow off-screen
    because `ft.Row` doesn't scroll or wrap by default. Wrapping them in this
    helper makes the row scroll horizontally when there isn't room, so every
    button stays reachable. The title (first control) takes `expand=True` so it
    fills available width and pushes the buttons to the right, matching the
    existing visual layout — but the row scrolls when the buttons don't fit.
    """
    items = list(controls)
    if items and expand_title:
        # Mark the first control as expanding so it fills available width.
        # We re-create it as the same type with expand=True if it's a Text;
        # otherwise we wrap it in a Container with expand=True.
        first = items[0]
        if isinstance(first, ft.Text):
            first.expand = True
        else:
            items[0] = ft.Container(content=first, expand=True)
    return ft.Row(items, scroll=ft.ScrollMode.AUTO, spacing=0)


def scrollbar_row(*controls: ft.Control) -> ft.Row:
    """Like `toolbar()` but doesn't expand the first control — useful for pure
    action-button rows (no title) that should just left-align and scroll."""
    return ft.Row(list(controls), scroll=ft.ScrollMode.AUTO, spacing=0)


def confirm(page: ft.Page, title: str, body: str, on_yes: Callable[[], Awaitable | None],
            typed: str | None = None, yes_label: str = "Confirm") -> None:
    """Confirm dialog. If `typed` is given, the user must type it to proceed."""
    field = ft.TextField(hint_text=f'Type "{typed}"', autofocus=True) if typed else None
    ok = ft.FilledButton(yes_label, disabled=bool(typed))

    def on_change(_):
        ok.disabled = field.value != typed
        page.update()

    async def yes(_):
        page.pop_dialog()
        res = on_yes()
        if asyncio.iscoroutine(res):
            await res

    ok.on_click = yes
    if field:
        field.on_change = on_change
    page.show_dialog(ft.AlertDialog(
        title=ft.Text(title),
        content=ft.Column([ft.Text(body)] + ([field] if field else []), tight=True),
        actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()), ok]))


def empty_state(icon, title: str, subtitle: str = "") -> ft.Control:
    return ft.Container(
        alignment=ft.Alignment.CENTER, expand=True, padding=SPACE * 2,
        content=ft.Column(
            [ft.Icon(icon, size=56, opacity=0.5),
             ft.Text(title, size=18, weight=ft.FontWeight.W_600),
             ft.Text(subtitle, opacity=0.7, text_align=ft.TextAlign.CENTER)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True))


def card(content: ft.Control) -> ft.Container:
    return ft.Container(content=content, padding=SPACE, border_radius=RADIUS,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH)


class ConnectionBanner(ft.Container):
    """Shown whenever the SSH session is not connected."""

    def __init__(self, on_reconnect: Callable[[], Awaitable | None]) -> None:
        self._label = ft.Text("Not connected", expand=True)
        self._btn = ft.TextButton("Reconnect", height=MIN_TAP)

        async def click(_):
            res = on_reconnect()
            if asyncio.iscoroutine(res):
                await res
        self._btn.on_click = click
        super().__init__(
            content=ft.Row([ft.Icon(ft.Icons.CLOUD_OFF), self._label, self._btn]),
            padding=ft.Padding.symmetric(horizontal=SPACE),
            bgcolor=ft.Colors.ERROR_CONTAINER, visible=True)

    def set_status(self, status: str) -> None:
        self.visible = status != "connected"
        self._label.value = {"connecting": "Connecting…",
                             "reconnecting": "Connection lost, reconnecting…"
                             }.get(status, "Not connected")
        self._btn.visible = status == "disconnected"
        try:
            self.update()
        except Exception:  # noqa: BLE001  (not yet mounted)
            pass
