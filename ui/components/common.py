"""Reusable widgets: toast, confirm dialog, connection banner, empty state,
toolbar, status pill, skeleton loader, section header.

All components pull from `ui.theme` design tokens (spacing scale, radius
scale, elevation, semantic state colors) so the visual language stays
consistent without pages hard-coding numbers.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

import flet as ft

from services.i18n import translate
from ..theme import (
    ELEVATION_FLAT, ELEVATION_LOW, ELEVATION_MEDIUM, ELEVATION_HIGH,
    ICON_SIZE, ICON_SIZE_LG, ICON_SIZE_XL, MIN_TAP, RADIUS, RADIUS_MD,
    RADIUS_PILL, RADIUS_SM, SPACE, SPACE_LG, SPACE_SM, SPACE_XL, SPACE_XS,
    state_color, status_to_state,
)


def safe_update(page) -> None:
    """Safely update the page. Does nothing if the page is not ready."""
    try:
        page.update()
    except Exception:  # noqa: BLE001
        pass


def _safe_pop(page: ft.Page) -> None:
    """Safely pop the top dialog. Does nothing if there's no dialog on the stack."""
    try:
        page.pop_dialog()
    except Exception:  # noqa: BLE001
        pass


def toast(page: ft.Page, text: str, error: bool = False) -> None:
    """Show a transient SnackBar.

    Modern UX: the SnackBar is the standard "fire and forget" feedback
    channel — short, dismissible, non-blocking. We give error toasts an
    error-container background so they read as "something went wrong"
    without being aggressive (no flashing red).
    """
    page.show_dialog(ft.SnackBar(
        ft.Text(text), bgcolor=ft.Colors.ERROR_CONTAINER if error else None))


def toolbar(*controls: ft.Control, expand_title: bool = True) -> ft.Row:
    """A horizontally-scrollable row for page topbars and action bars.

    on narrow phones, action-button rows used to overflow off-screen
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
    return ft.Row(items, scroll=ft.ScrollMode.AUTO, spacing=SPACE_XS)


def scrollbar_row(*controls: ft.Control) -> ft.Row:
    """Like `toolbar()` but doesn't expand the first control — useful for pure
    action-button rows (no title) that should just left-align and scroll."""
    return ft.Row(list(controls), scroll=ft.ScrollMode.AUTO, spacing=SPACE_XS)


def confirm(page: ft.Page, title: str, body: str, on_yes: Callable[[], Awaitable | None],
            typed: str | None = None, yes_label: str = "Confirm") -> None:
    """Confirm dialog. If `typed` is given, the user must type it to proceed.

    Modern UX: the body text uses the secondary text color (so it doesn't
    compete with the title), and the destructive action button is red so
    users pause before clicking. The Cancel button is on the leading edge
    (left in LTR, right in RTL) and Confirm on the trailing edge — this is
    Material 3's standard dialog action order.
    """
    is_destructive = bool(typed) or yes_label.lower() in (
        "delete", "remove", "destroy", "discard", "force quit", "kill")
    field = ft.TextField(hint_text=f'Type "{typed}"', autofocus=True) if typed else None
    ok = ft.FilledButton(
        yes_label, disabled=bool(typed),
        bgcolor=ft.Colors.ERROR if is_destructive else None,
    )

    def on_change(_):
        ok.disabled = field.value != typed
        page.update()

    async def yes(_):
        _safe_pop(page)
        res = on_yes()
        if asyncio.iscoroutine(res):
            await res

    ok.on_click = yes
    if field:
        field.on_change = on_change
    page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text(title, weight=ft.FontWeight.W_600),
        content=ft.Column(
            [ft.Text(body, color=ft.Colors.ON_SURFACE_VARIANT)] + ([field] if field else []),
            tight=True, spacing=SPACE_SM),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ok,
        ]))


def empty_state(icon, title: str, subtitle: str = "",
                action_label: str | None = None,
                on_action: Optional[Callable[[], None]] = None) -> ft.Control:
    """Modern empty state.

    Per Pencil&Paper / setproduct empty-state UX guidance:
      - center the content vertically and horizontally
      - the icon is decorative (large, soft opacity)
      - the title is the visually dominant element
      - the subtitle gives context (NOT a blame message — never "you didn't…")
      - one primary action the user can take from this state

    The action button is optional — pass `action_label` + `on_action` to
    render a FilledTonalButton below the subtitle.
    """
    children = [
        ft.Icon(icon, size=ICON_SIZE_XL, opacity=0.45,
                color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Text(title, size=18, weight=ft.FontWeight.W_600, text_align=ft.TextAlign.CENTER),
    ]
    if subtitle:
        children.append(ft.Text(subtitle, opacity=0.7, size=13,
                                text_align=ft.TextAlign.CENTER))
    if action_label and on_action:
        children.append(ft.Container(
            padding=ft.Padding.only(top=SPACE_SM),
            content=ft.FilledTonalButton(action_label, icon=ft.Icons.ADD, on_click=on_action),
        ))
    return ft.Container(
        alignment=ft.Alignment.CENTER, expand=True, padding=SPACE_XL,
        content=ft.Column(
            children, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER, tight=True, spacing=SPACE_SM))


def card(content: ft.Control, padding: int = SPACE, elevation: int = ELEVATION_FLAT,
         bgcolor: str = ft.Colors.SURFACE_CONTAINER_HIGH,
         border_radius: int = RADIUS) -> ft.Container:
    """A Material 3 card.

    Defaults to a *filled* card (elevation=0) which is M3's recommended
    default — calm, low visual noise. Pass `elevation=ELEVATION_LOW` for a
    raised card (hover or emphasis), `ELEVATION_MEDIUM` for dialogs.

    The corner radius defaults to RADIUS (16dp) — Tailwind's rounded-2xl,
    which feels modern without being too soft. Cards inside other cards
    should pick RADIUS_MD (12dp) to feel like nested surfaces.

    Flet 1.x note: `Container` doesn't have an `elevation` field directly;
    we translate the elevation token into a `BoxShadow` with appropriate
    blur/spread so the card visually lifts off the surface.
    """
    # Map elevation token → BoxShadow (Flet 1.x shadow model).
    # Material 3's elevation scale: 1dp=soft blur 2, 3dp=blur 6, 5dp=blur 12.
    shadow = None
    if elevation > ELEVATION_FLAT:
        blur = {ELEVATION_LOW: 3, ELEVATION_MEDIUM: 6, ELEVATION_HIGH: 12}.get(elevation, 6)
        shadow = ft.BoxShadow(
            blur_radius=blur, spread_radius=0,
            color=ft.Colors.with_opacity(0.15 + (elevation * 0.03), ft.Colors.BLACK),
            offset=ft.Offset(0, elevation * 0.5),  # subtle downward offset
        )
    # Use ft.Border.all — Flet 1.x moved `border.all` to a class method on
    # `Border` (not the module). The previous `ft.border.all(...)` form is gone.
    border = None
    if elevation == ELEVATION_FLAT:
        # A subtle 1dp border in the surface-variant color gives the card
        # a crisp edge without the heavy shadow of an outlined card.
        border = ft.Border.all(1, ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE))
    return ft.Container(
        content=content, padding=padding, border_radius=border_radius,
        bgcolor=bgcolor, shadow=shadow, border=border,
    )


def section_header(title: str, subtitle: str = "",
                    trailing: ft.Control | None = None) -> ft.Control:
    """A page-section header: title + optional subtitle + optional trailing
    action (e.g. "See all" or a small icon button).

    Use this to break a long page into sections. The title uses size 18 /
    W_600 (the same weight as settings headers), the subtitle is small and
    muted. The trailing control floats to the end.
    """
    leading = ft.Column([ft.Text(title, size=18, weight=ft.FontWeight.W_600)],
                        spacing=0, expand=True)
    if subtitle:
        leading.controls.append(
            ft.Text(subtitle, size=12, opacity=0.65, no_wrap=True))
    items = [leading]
    if trailing is not None:
        items.append(trailing)
    return ft.Container(
        padding=ft.Padding.only(top=SPACE_LG, bottom=SPACE_SM),
        content=ft.Row(items, vertical_alignment=ft.CrossAxisAlignment.CENTER))


def status_pill(label: str, state: str = "neutral", *,
                dot: bool = True, icon: str | None = None) -> ft.Control:
    """A small status pill — colored background + label.

    Used for container status, connection state, cron job state, etc.

    Args:
        label: visible text (e.g. "running", "Connected", "0 errors").
        state: one of ok/info/warning/error/critical/neutral/muted, OR a
               raw status string like "running"/"exited" — we'll map it
               via status_to_state() so callers don't have to.
        dot: render a small colored dot before the label (Material 3 style).
        icon: optional Material icon name to render instead of the dot.

    The pill uses the semantic container+on-container colors from
    `theme.STATE_COLORS`. Pill height is 22dp — small enough to fit
    inside list rows without breaking alignment.
    """
    if state not in ("ok", "info", "warning", "error", "critical", "neutral", "muted"):
        state = status_to_state(state)
    container_bg = state_color(state, "container")
    on_color = state_color(state, "on")
    plain = state_color(state, "plain")
    children: list[ft.Control] = []
    if icon:
        children.append(ft.Icon(icon, size=12, color=plain))
    elif dot:
        children.append(ft.Container(
            width=6, height=6, border_radius=3, bgcolor=plain))
    children.append(ft.Text(label, size=11, weight=ft.FontWeight.W_600, color=on_color))
    return ft.Container(
        content=ft.Row(children, spacing=SPACE_XS, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
        border_radius=RADIUS_PILL, bgcolor=container_bg, height=22)


def skeleton_row(width: int = 200, height: int = 14) -> ft.Control:
    """A single skeleton row — a soft grey rectangle that mimics a line of
    text being loaded.

    Per NN/g skeleton-screen guidance: use skeletons for content placeholders
    (the user knows roughly what shape is coming), and reserve spinners for
    full-page loads or indeterminate waits.
    """
    return ft.Container(
        width=width, height=height, border_radius=RADIUS_SM,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, opacity=0.7)


def skeleton_list(rows: int = 5, line_height: int = 14) -> ft.Control:
    """A skeleton list — N stacked skeleton rows separated by spacing,
    mimicking a list view that's still loading its data.

    Each row has a small leading block (icon placeholder) + 2 lines of
    varying width so the eye recognizes the list-item shape.
    """
    items: list[ft.Control] = []
    for i in range(rows):
        items.append(ft.Row([
            ft.Container(width=36, height=36, border_radius=RADIUS_MD,
                          bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, opacity=0.7),
            ft.Column([
                skeleton_row(width=180 + (i * 17) % 80, height=line_height),
                skeleton_row(width=110 + (i * 23) % 60, height=line_height - 2),
            ], spacing=4, expand=True),
        ], spacing=SPACE_SM))
    return ft.Column(items, spacing=SPACE)


def loading_state(label: str = "Loading…") -> ft.Control:
    """A modern centered loading state — small spinner + label.

    Use this for full-page loads where the data shape is unknown
    (e.g. "fetching server info"). For list placeholders, prefer
    `skeleton_list()` because it shows the user what's coming.
    """
    return ft.Container(
        alignment=ft.Alignment.CENTER, expand=True, padding=SPACE_XL,
        content=ft.Column([
            ft.ProgressRing(width=32, height=32),
            ft.Text(label, size=13, opacity=0.6),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=SPACE_SM, tight=True))


def error_state(message: str, retry: Optional[Callable[[], None]] = None) -> ft.Control:
    """A modern centered error state — red icon + message + optional Retry.

    Per setproduct's empty-state UX guidance: clearly explain what happened,
    offer a corrective action (Retry), and don't blame the user.
    """
    children: list[ft.Control] = [
        ft.Icon(ft.Icons.ERROR_OUTLINE, size=ICON_SIZE_XL,
                color=ft.Colors.ERROR, opacity=0.85),
        ft.Text("Something went wrong", size=16, weight=ft.FontWeight.W_600),
        ft.Text(message, size=12, opacity=0.7, text_align=ft.TextAlign.CENTER,
                selectable=True),
    ]
    if retry:
        children.append(ft.Container(
            padding=ft.Padding.only(top=SPACE_SM),
            content=ft.OutlinedButton("Retry", icon=ft.Icons.REFRESH, on_click=lambda _: retry()),
        ))
    return ft.Container(
        alignment=ft.Alignment.CENTER, expand=True, padding=SPACE_XL,
        content=ft.Column(
            children, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER, tight=True, spacing=SPACE_SM))


class ConnectionBanner(ft.Container):
    """Shown whenever the SSH session is not connected.

    Modern UX:
      - calm error-container background (not a flashing red)
      - colored leading dot reflects the live status
        (amber=connecting, red=disconnected)
      - the Reconnect button only appears when the user can actually
        reconnect (status == "disconnected"). Otherwise it's hidden so
        the banner doesn't tease an unavailable action.

    Picks up Persian translations automatically when the user's language
    is 'fa' so the banner reads «در حال اتصال…» / «در حال اتصال مجدد…»
    instead of the English text.
    """

    # English status → Persian translation key, used when language=fa.
    # The Persian strings all live in services/i18n.TRANSLATIONS.
    _STATUS_LABELS = {
        "connecting": "Connecting…",
        "reconnecting": "Connection lost, reconnecting…",
        "disconnected": "Not connected",
        "connected": "Connected",
    }

    def __init__(self, on_reconnect: Callable[[], Awaitable | None],
                 language: str = "en") -> None:
        self._lang = language
        self._t = lambda s: translate(s, language)  # noqa: E731
        self._label = ft.Text(self._t("Not connected"), expand=True, size=13)
        # Flet 1.x TextButton uses `content` (a string or Control), not `text`.
        # We store the button and update its `.content` field when the language
        # changes. The `icon` argument is supported directly.
        self._btn = ft.TextButton(self._t("Reconnect"), height=MIN_TAP, icon=ft.Icons.REFRESH)
        # Colored status dot — reflects the live connection state.
        self._dot = ft.Container(width=8, height=8, border_radius=4,
                                  bgcolor=ft.Colors.RED)

        async def click(_):
            res = on_reconnect()
            if asyncio.iscoroutine(res):
                await res
        self._btn.on_click = click
        super().__init__(
            content=ft.Row(
                [self._dot, self._label, self._btn],
                spacing=SPACE_SM, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=SPACE_LG, vertical=SPACE_SM),
            bgcolor=ft.Colors.ERROR_CONTAINER, visible=True,
            border_radius=0,  # banner spans the full width edge-to-edge
        )

    def set_language(self, language: str) -> None:
        """Switch the banner's language without rebuilding. Called when the
        user changes language in Settings."""
        self._lang = language
        self._t = lambda s: translate(s, language)  # noqa: E731

    def set_status(self, status: str) -> None:
        self.visible = status != "connected"
        # Look up the human-readable label, translated.
        label_key = self._STATUS_LABELS.get(status, "Not connected")
        self._label.value = self._t(label_key)
        self._btn.visible = status == "disconnected"
        # Re-translate the button label too, in case it changed.
        # TextButton.content holds the label string in Flet 1.x.
        self._btn.content = self._t("Reconnect")
        # Update the status dot color to match the live state.
        # amber while connecting, red when fully disconnected.
        if status == "connecting" or status == "reconnecting":
            self._dot.bgcolor = ft.Colors.AMBER
        elif status == "disconnected":
            self._dot.bgcolor = ft.Colors.RED
        else:
            # 'connected' (banner is hidden anyway)
            self._dot.bgcolor = ft.Colors.GREEN
        try:
            self.update()
        except Exception:  # noqa: BLE001  (not yet mounted)
            pass


# Convenience: build a top-of-page header with a title and trailing action
# buttons. Used by every page's _topbar.
def page_header(title: str, subtitle: str = "",
                actions: list[ft.Control] | None = None) -> ft.Control:
    """Page-level header: large title + subtitle + trailing actions.

    Modern UX (Linear / Vercel style):
      - title is large (22pt) and bold
      - subtitle is muted and small (12pt) — gives context without competing
      - actions are right-aligned (LTR) / left-aligned (RTL)
      - everything sits in a single row that scrolls horizontally on overflow
    """
    leading = ft.Column(
        [ft.Text(title, size=22, weight=ft.FontWeight.W_600, no_wrap=True)],
        spacing=0, expand=True, alignment=ft.MainAxisAlignment.CENTER,
    )
    if subtitle:
        leading.controls.insert(1, ft.Text(
            subtitle, size=12, opacity=0.65, no_wrap=True))
    items = [leading] + (actions or [])
    return ft.Row(items, scroll=ft.ScrollMode.AUTO, spacing=SPACE_XS,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)

