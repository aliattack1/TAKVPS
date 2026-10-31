"""Terminal UI: tabs of PTY sessions rendered from the emulator in services/terminal.py.

Input model (first try): a one-line TextField holds an invisible sentinel character. Whatever the
user types is diffed against the sentinel and sent straight to the PTY (so passwords, vim, etc.
work); deleting the sentinel sends Backspace. A toolbar provides Esc/Tab/Ctrl/arrows/symbols.

Phase 13 additions:
- Persistent sessions: if `tmux` is on the server, new terminals attach to a named tmux session
  so they survive SSH drops. A "Sessions" button lists/creates/attaches/kills them.
- Long-press on any output row → "Send to AI" with that row + a few lines of surrounding context.
- Smart auto-scroll: the output list follows new output only while you're already at the bottom.
  Scrolling up to read older output no longer gets yanked back when new data arrives.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import flet as ft

from core.errors import TakError
from services.terminal import (BOLD, ITAL, REV, UNDER, Screen, TerminalSession, color_hex, ctrl_char,
                               key_sequence)
from services import tmux as tmux_svc
from services import term_search
from services import mouse as mouse_svc
from .. import theme
from ..components.common import toast
from ..context import AppContext

BG, FG = "#0b0f14", "#d6dde6"
SENTINEL = "\u200b"
MAX_SCROLLBACK_CONTROLS = 600
# Auto-scroll tolerance: if the user is within this many px of the bottom, we treat them as
# "at the bottom" and keep following. Beyond that, we leave their scroll position alone.
SCROLL_BOTTOM_TOLERANCE_PX = 60


def _span(text: str, style: tuple) -> ft.TextSpan:
    fg, bg, fl = style
    if fl & REV:
        fg_c, bg_c = color_hex(bg) or BG, color_hex(fg) or FG
    else:
        fg_c, bg_c = color_hex(fg), color_hex(bg)
    return ft.TextSpan(text, ft.TextStyle(
        color=fg_c, bgcolor=bg_c, weight=ft.FontWeight.W_700 if fl & BOLD else None,
        italic=bool(fl & ITAL),
        decoration=ft.TextDecoration.UNDERLINE if fl & UNDER else None))


class TerminalController:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page, self.s = ctx, ctx.page, ctx.store.data
        self.sessions: list[TerminalSession] = []
        self.active = 0
        self.view: Optional["TerminalView"] = None
        self.ctrl_armed = False
        self._clip = None
        # Phase 13.1: persistent sessions via tmux on the server.
        self.tmux_installed: Optional[bool] = None    # None = unknown (probed lazily)
        self.tmux_sessions: list[tmux_svc.TmuxSession] = []
        self.last_session_name: str = ""               # the name we last used, for "Reattach"

    # ---------------------------------------------------------------- sessions
    @property
    def session(self) -> Optional[TerminalSession]:
        return self.sessions[self.active] if self.sessions else None

    def size(self) -> tuple[int, int]:
        """Estimate cols/rows from the window size (monospace advance ≈ 0.6 × font size)."""
        fs = self.s.terminal_font_size
        wide = (self.page.width or 400) >= theme.PHONE_MAX_WIDTH
        w = (self.page.width or 400) - (96 if wide else 0) - 20
        h = (self.page.height or 700) - (300 if wide else 380)
        return max(20, int(w / (fs * 0.6))), max(8, int(h / (fs * 1.3)))

    def build(self) -> ft.Control:
        self.view = TerminalView(self)
        self.page.run_task(self.ensure_session)
        # Probe tmux once on first build of the terminal page.
        self.page.run_task(self.refresh_tmux)
        return self.view.control

    async def ensure_session(self) -> None:
        if not self.sessions and self.ctx.ssh.status.value == "connected":
            await self.new_session()

    async def new_session(self, tmux_name: Optional[str] = None) -> None:
        """Open a new PTY. If `tmux_name` is given and tmux is installed, attach to that
        named session (creating it if needed) so the session persists across SSH drops."""
        if self.ctx.ssh.status.value != "connected":
            toast(self.page, "Not connected.", error=True)
            return
        # Lazy-probe tmux the first time someone asks for a persistent session.
        if tmux_name and self.tmux_installed is None:
            await self.refresh_tmux()
        command: Optional[str] = None
        if tmux_name:
            if not self.tmux_installed:
                toast(self.page, "tmux isn't installed on this server — opening a normal shell.", error=False)
            else:
                try:
                    command = tmux_svc.new_or_attach_command(tmux_name)
                    self.last_session_name = tmux_name
                except ValueError:
                    toast(self.page, "That session name has invalid characters.", error=True)
                    return
        cols, rows = self.size()
        sess = TerminalSession(self.ctx.ssh, self.page.run_task, cols, rows, self._on_update,
                               command=command)
        try:
            await sess.start()
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        except Exception:  # noqa: BLE001
            toast(self.page, "Could not start a shell on the server.", error=True)
            return
        # Make the tab name reflect the tmux session if we attached to one.
        if tmux_name:
            sess.name = f"⏵ {tmux_name}"
        self.sessions.append(sess)
        self.active = len(self.sessions) - 1
        if self.view:
            self.view.attach()
            self.page.update()

    # ---- Phase 13.1: tmux session management ----
    async def refresh_tmux(self) -> None:
        """Probe tmux presence and refresh the list of running tmux sessions."""
        if self.ctx.ssh.status.value != "connected":
            self.tmux_installed = None
            self.tmux_sessions = []
            return
        try:
            r = await self.ctx.ssh.run("command -v tmux", timeout=8)
            self.tmux_installed = tmux_svc.is_installed(r)
        except TakError:
            self.tmux_installed = False
        if not self.tmux_installed:
            self.tmux_sessions = []
            return
        try:
            r = await self.ctx.ssh.run(tmux_svc.list_command(), timeout=8)
            self.tmux_sessions = tmux_svc.parse_sessions(r.stdout)
        except TakError:
            self.tmux_sessions = []

    async def open_sessions_dialog(self) -> None:
        """Show the 'Persistent sessions' dialog: list / create / attach / kill."""
        if self.ctx.ssh.status.value != "connected":
            toast(self.page, "Not connected.", error=True)
            return
        await self.refresh_tmux()
        if self.tmux_installed is False:
            toast(self.page, "tmux isn't installed on this server. Install it (`apt install tmux` etc.) "
                  "to enable persistent sessions.", error=True)
            return
        view = self.view
        if view is None:
            return
        view.show_sessions_dialog()

    async def kill_tmux_session(self, name: str) -> None:
        try:
            cmd = tmux_svc.kill_session_command(name)
            await self.ctx.ssh.run(cmd, timeout=10)
            toast(self.page, f"Killed tmux session '{name}'.")
        except (TakError, ValueError) as e:
            toast(self.page, getattr(e, "message", str(e)), error=True)
            return
        await self.refresh_tmux()
        if self.view:
            self.view.refresh_sessions_dialog()

    def close_session(self, i: int) -> None:
        self.sessions.pop(i).close()
        self.active = max(0, min(self.active, len(self.sessions) - 1))
        if self.view:
            self.view.attach()
            self.page.update()

    def select(self, i: int) -> None:
        self.active = i
        if self.view:
            self.view.attach()
            self.page.update()

    def _on_update(self, sess: TerminalSession) -> None:
        if self.view and sess is self.session:
            self.view.request_render()

    # ---------------------------------------------------------------- input
    def send(self, data: str) -> None:
        if (s := self.session) and s.alive:
            s.write(data)

    def send_key(self, name: str) -> None:
        s = self.session
        self.send(key_sequence(name, bool(s and s.screen.app_cursor)))

    def send_text(self, text: str) -> None:
        if self.ctrl_armed and len(text) == 1 and (c := ctrl_char(text)):
            text = c
            self.ctrl_armed = False
            if self.view:
                self.view.refresh_ctrl()
        self.send(text)

    def toggle_ctrl(self) -> None:
        self.ctrl_armed = not self.ctrl_armed
        if self.view:
            self.view.refresh_ctrl()
            self.page.update()

    def on_key(self, e) -> None:
        """Phase 15 terminal overhaul: capture ALL keyboard events directly.

        This replaces the old sentinel-TextField approach. Every key —
        printable, function keys, Tab, Ctrl+anything, arrows, Home/End/Ins —
        is routed straight to the PTY. No text field is needed for typing
        on desktop; the mobile toolbar still works for touch devices.

        The old TextField is kept as a hidden fallback (for soft keyboards on
        phones where on_keyboard_event may not fire for printable chars).
        """
        key = getattr(e, "key", "")
        shift = getattr(e, "shift", False)
        ctrl = getattr(e, "ctrl", False)
        alt = getattr(e, "alt", False)
        meta = getattr(e, "meta", False)
        s = self.session
        if s is None or not s.alive:
            return

        # ---- Function keys F1-F12 ----
        fn_map = {"F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4",
                  "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8",
                  "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12"}
        if key in fn_map:
            self.send_key(fn_map[key])
            return

        # ---- Special keys with Shift/Ctrl/Alt modifier combos ----
        special_names = {
            "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left",
            "ArrowRight": "right", "Escape": "esc", "Home": "home",
            "End": "end", "Page Up": "pgup", "Page Down": "pgdn",
            "Delete": "del", "Insert": "insert",
        }
        if key in special_names:
            base = special_names[key]
            # Ctrl+arrow = ESC [ 5 <dir>  (xterm sequence for Ctrl+arrow)
            if ctrl:
                self.send(f"\x1b[1;5{'ABCD'['up down left right'.index(base)]}" if base in ("up", "down", "left", "right") else key_sequence(base))
            elif shift and base in ("up", "down", "left", "right"):
                self.send(f"\x1b[1;2{'ABCD'['up down left right'.index(base)]}")
            elif alt and base in ("up", "down", "left", "right"):
                self.send(f"\x1b[1;3{'ABCD'['up down left right'.index(base)]}")
            else:
                self.send_key(base)
            return

        # ---- Tab (intercept so the browser doesn't use it for focus nav) ----
        if key == "Tab":
            if shift:
                self.send_key("shift_tab")
            else:
                self.send_key("tab")
            return

        # ---- Enter / Backspace ----
        if key == "Enter":
            self.send_key("enter")
            return
        if key == "Backspace":
            self.send_key("backspace")
            return

        # ---- Ctrl+letter (and Ctrl+@, Ctrl+[, etc.) ----
        if ctrl and len(key) == 1:
            c = ctrl_char(key)
            if c:
                self.send(c)
                return

        # ---- Alt+letter (ESC prefix) ----
        if alt and len(key) == 1 and key.isalpha():
            self.send("\x1b" + key)
            return

        # ---- Printable characters (length 1, not a control key) ----
        # We only handle single-char keys here; multi-char key names (like
        # "Space", "Enter") are handled above. Some browsers send " " for
        # space, some send "Space" — both are covered.
        if key == " " or key == "Space":
            self.send(" ")
            return
        if len(key) == 1 and key.isprintable():
            self.send(key)
            return

    def set_visible(self, visible: bool) -> None:
        self.page.on_keyboard_event = self.on_key if visible else None
        if not visible:
            self.view = None
        elif self.view:
            # Re-focus the output container so keyboard events are captured
            # immediately on page switch. The output Container has on_tap_down
            # which calls _on_tap_down → but we also need focus for keyboard.
            # On desktop, on_keyboard_event fires regardless of focus.
            # On mobile, the hidden TextField handles soft-keyboard input.
            pass

    def on_resize(self) -> None:
        if not self.sessions:
            return
        cols, rows = self.size()
        for s in self.sessions:
            s.resize(cols, rows)
        if self.view:
            self.view.attach()
            self.page.update()

    # ---------------------------------------------------------------- clipboard
    async def _clipboard(self):
        if self._clip is None:
            self._clip = ft.Clipboard()
            self.page.services.append(self._clip)
            self.page.update()
        return self._clip

    async def copy_output(self) -> None:
        if not self.session:
            return
        try:
            await (await self._clipboard()).set(self.session.screen.text())
            toast(self.page, "Terminal output copied.")
        except Exception:  # noqa: BLE001
            toast(self.page, "Couldn't access the clipboard here.", error=True)

    async def copy_text(self, text: str) -> None:
        try:
            await (await self._clipboard()).set(text)
            toast(self.page, "Copied.")
        except Exception:  # noqa: BLE001
            toast(self.page, "Couldn't access the clipboard here.", error=True)

    def tail(self, lines: int = 60) -> str:
        s = self.session
        return "\n".join(s.screen.text().splitlines()[-lines:]) if s else ""

    def ask_ai_about_output(self) -> None:
        tail = self.tail(60)
        if not tail.strip():
            toast(self.page, "The terminal is empty.")
            return
        self.ctx.open_chat("Explain this terminal output and tell me what to do next:\n\n```\n" + tail + "\n```")

    async def paste(self) -> None:
        try:
            text = await (await self._clipboard()).get()
        except Exception:  # noqa: BLE001
            toast(self.page, "Couldn't read the clipboard here.", error=True)
            return
        if text and (s := self.session):
            text = text.replace("\r\n", "\n").replace("\n", "\r")
            self.send(f"\x1b[200~{text}\x1b[201~" if s.screen.bracketed else text)

    def clear(self) -> None:
        if s := self.session:
            s.screen.scrollback.clear()
            s.screen.scrolled_total = 0
            self.send("\x0c")
            if self.view:
                self.view.attach()
                self.page.update()

    def change_font(self, d: int) -> None:
        self.s.terminal_font_size = max(8, min(22, self.s.terminal_font_size + d))
        self.ctx.store.save()
        self.on_resize()
        if self.view:
            self.view.attach()
            self.page.update()


class TerminalView:
    def __init__(self, ctl: TerminalController) -> None:
        self.ctl, self.page = ctl, ctl.page
        self._pending = False
        self._seen = 0
        self._sigs: list = []
        self._rows: list[ft.Text] = []
        self._scroll_ctrls: list[ft.Control] = []
        self._alt = False
        # Phase 13.8: smart auto-scroll. We track whether the user is currently at
        # the bottom of the output list. If they scrolled up to read older output,
        # we don't yank them back when new data arrives.
        self._at_bottom = True

        self.tabs = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=4)
        self.screen_col = ft.Column(spacing=0)
        # auto_scroll is OFF now; we scroll manually in render() only when _at_bottom.
        self.out = ft.ListView(expand=True, spacing=0, auto_scroll=False,
                               on_scroll=self._on_scroll)
        # Phase 14 Part 9: wrap the output ListView in a Container with mouse
        # handlers so we can capture clicks and wheel-scrolles when the remote
        # app (vim/less/tmux) has enabled mouse reporting.
        self.output_container = ft.Container(
            content=self.out, bgcolor=BG, expand=True, padding=6, border_radius=8,
            on_tap_down=self._on_tap_down,
            on_hover=self._on_hover,
            on_click=self._on_click,
        )
        self.title = ft.Text("", size=11, opacity=0.7, no_wrap=True)
        self.ctrl_btn = ft.TextButton("Ctrl", height=44, on_click=lambda _: ctl.toggle_ctrl())
        self.input = self._make_input()
        # Phase 14 Part 6: search-in-scrollback overlay. Hidden by default;
        # the Find toolbar button toggles it.
        self._find_bar = self._make_find_bar()
        self._find_matches: list = []          # list of term_search.Match
        self._find_current: int | None = None   # index into _find_matches
        self.control = ft.Column([
            ft.Container(self.tabs, padding=ft.Padding.symmetric(horizontal=4)),
            self._find_bar,
            self.output_container,
            self.title, self._keys(), self.input], expand=True, spacing=4)
        self.attach()

    # ---------------------------------------------------------------- scroll tracking
    def _on_scroll(self, e) -> None:
        """Track whether the user is at the bottom of the output list.

        Flet fires on_scroll with `event_name` in {'scroll', 'end'}.
        We use `p` (scroll offset as a fraction of the total) and a small tolerance
        to decide. When the user scrolls back to the bottom, we resume auto-scrolling.
        """
        try:
            offset = float(getattr(e, "offset", 0) or 0)
            max_extent = float(getattr(e, "max_scroll_extent", 0) or 0)
        except (TypeError, ValueError):
            return
        if max_extent <= 0:
            # No scrollable content (everything fits in the viewport).
            self._at_bottom = True
            return
        # The exact pixel value where "at bottom" begins. We tolerate a few px
        # so the user doesn't have to be pixel-perfect.
        threshold = max(0.0, max_extent - SCROLL_BOTTOM_TOLERANCE_PX)
        self._at_bottom = offset >= threshold

    def _maybe_scroll_to_bottom(self) -> None:
        """Called from render() after new output is added. Only scrolls if the user
        was already at the bottom, so reading old output isn't interrupted."""
        if not self._at_bottom:
            return
        # Flet's ListView doesn't expose scroll_to in 1.0 yet (auto_scroll=True is the
        # only built-in way), so we toggle auto_scroll off→on briefly to force a jump
        # to the bottom. Cheap and works on every Flet version.
        try:
            self.out.auto_scroll = True
            self.page.update()
            # Switch back off so the next manual scroll doesn't get yanked.
            self.out.auto_scroll = False
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- Phase 14 Part 9: mouse
    def _pixel_to_cell(self, local_pos) -> tuple[int, int]:
        """Convert a pixel position (from a Flet TapEvent/HoverEvent) to a
        1-based terminal cell coordinate (col, row)."""
        if local_pos is None:
            return (1, 1)
        # The output Container has 6px padding around the ListView.
        px = float(getattr(local_pos, "x", 0) or 0) - 6
        py = float(getattr(local_pos, "y", 0) or 0) - 6
        fs = self.ctl.s.terminal_font_size
        # Cell width ≈ 0.6 × font size (matches TerminalController.size()).
        col = int(px / (fs * 0.6)) + 1
        row = int(py / (fs * 1.3)) + 1
        return (max(1, col), max(1, row))

    def _on_tap_down(self, e) -> None:
        """Phase 14 Part 9: a tap (click) on the terminal output. If the remote
        app has enabled mouse reporting, send the SGR mouse sequence."""
        s = self.ctl.session
        if s is None or not s.screen.mouse_reporting:
            return     # app hasn't requested mouse events — let the tap do nothing
        col, row = self._pixel_to_cell(getattr(e, "local_position", None))
        # We don't know which button from Flet's TapEvent; default to left.
        # Flet's on_tap_down fires on any button on desktop.
        seq = mouse_svc.encode_click(mouse_svc.BUTTON_LEFT, col, row)
        s.write(seq)
        # If mode 1003 (any-event) is on, also send a release.
        if s.screen.mouse_any:
            rel_seq = mouse_svc.encode_click(mouse_svc.BUTTON_LEFT, col, row, release=True)
            s.write(rel_seq)

    def _on_click(self, e) -> None:
        """Phase 14 Part 9: fallback click handler (some platforms fire on_click
        instead of on_tap_down). Same logic as _on_tap_down."""
        self._on_tap_down(e)

    def _on_hover(self, e) -> None:
        """Phase 14 Part 9: mouse motion. Only sent when mode 1003 (any-event)
        is on — vim uses this for mouse-aware cursor positioning."""
        s = self.ctl.session
        if s is None or not s.screen.mouse_reporting or not s.screen.mouse_any:
            return
        col, row = self._pixel_to_cell(getattr(e, "local_position", None))
        # Motion events use button code 35 (button 3 + release bit 32) in legacy
        # encoding, but SGR uses the actual button. For hover-without-button,
        # we send button 3 (right) which most apps treat as "no button".
        seq = mouse_svc.encode_click(mouse_svc.BUTTON_RIGHT, col, row)
        s.write(seq)

    # ---------------------------------------------------------------- Phase 14 Part 6: find-in-output
    def _make_find_bar(self) -> ft.Control:
        """A collapsible search bar above the terminal output. Hidden by default."""
        self._find_field = ft.TextField(
            hint_text="Find in output…", dense=True, expand=True, border_radius=8,
            on_change=lambda _: self._run_find(),
            on_submit=lambda _: self._find_next(),
            autofocus=True)
        self._find_status = ft.Text("", size=11, opacity=0.7, no_wrap=True)
        self._find_case = ft.IconButton(ft.Icons.TEXT_FIELDS, tooltip="Match case", width=36, height=36,
                                         selected=False, on_click=self._toggle_case)
        self._find_regex = ft.IconButton(ft.Icons.CODE, tooltip="Regex", width=36, height=36,
                                          selected=False, on_click=self._toggle_regex)
        bar = ft.Container(
            visible=False, padding=ft.Padding.symmetric(horizontal=4, vertical=2),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, border_radius=8,
            content=ft.Row([
                ft.Icon(ft.Icons.SEARCH, size=18, opacity=0.7),
                self._find_field,
                self._find_case,
                self._find_regex,
                ft.IconButton(ft.Icons.ARROW_UPWARD, tooltip="Previous", width=36, height=36,
                              on_click=lambda _: self._find_next(forward=False)),
                ft.IconButton(ft.Icons.ARROW_DOWNWARD, tooltip="Next", width=36, height=36,
                              on_click=lambda _: self._find_next(forward=True)),
                self._find_status,
                ft.IconButton(ft.Icons.CLOSE, tooltip="Close find", width=36, height=36,
                              on_click=lambda _: self._toggle_find(visible=False)),
            ], spacing=2, scroll=ft.ScrollMode.AUTO))
        return bar

    def _toggle_find(self, visible: bool | None = None) -> None:
        """Show or hide the find bar. When showing, focus the field."""
        if visible is None:
            visible = not self._find_bar.visible
        self._find_bar.visible = visible
        if visible:
            # Reset the search when opening.
            self._find_matches = []
            self._find_current = None
            self._find_status.value = ""
            self.page.run_task(self._focus_find)
        else:
            # Clear highlights when closing.
            self._find_matches = []
            self._find_current = None
            self.render()
        self.page.update()

    async def _focus_find(self) -> None:
        try:
            await self._find_field.focus()
        except Exception:  # noqa: BLE001
            pass

    def _toggle_case(self, e) -> None:
        e.control.selected = not e.control.selected
        self._run_find()

    def _toggle_regex(self, e) -> None:
        e.control.selected = not e.control.selected
        self._run_find()

    def _run_find(self) -> None:
        """Run the search and update matches + status. Called on every keystroke."""
        query = self._find_field.value or ""
        if not query:
            self._find_matches = []
            self._find_current = None
            self._find_status.value = ""
            self.render()
            return
        case = self._find_case.selected
        regex = self._find_regex.selected
        s = self.ctl.session
        if s is None:
            return
        scr = s.screen
        # Search through scrollback + grid. We need the combined row list.
        # scrollback is a deque; grid is a list. Both are lists of cells.
        all_rows = list(scr.scrollback) + scr.grid
        try:
            self._find_matches = term_search.search(all_rows, query,
                                                      case_sensitive=case, regex=regex)
        except Exception:  # noqa: BLE001
            self._find_matches = []
        # Jump to the first match.
        self._find_current = term_search.next_match(self._find_matches, None, forward=True) \
            if self._find_matches else None
        self._find_status.value = term_search.summary(self._find_matches, self._find_current)
        self.render()

    def _find_next(self, forward: bool = True) -> None:
        """Jump to the next/prev match. Wraps around."""
        if not self._find_matches:
            return
        self._find_current = term_search.next_match(self._find_matches,
                                                      self._find_current, forward=forward)
        self._find_status.value = term_search.summary(self._find_matches, self._find_current)
        self.render()

    # ---------------------------------------------------------------- build
    def _make_input(self) -> ft.TextField:
        """Phase 15 terminal overhaul: on desktop the TextField is hidden and
        keyboard input goes through page.on_keyboard_event directly. On mobile
        (where on_keyboard_event doesn't fire for printable chars), the
        sentinel TextField stays as the soft-keyboard input mechanism."""
        kw = dict(value=SENTINEL, hint_text="Tap to type", dense=True, on_change=self._on_input,
                  on_submit=self._on_submit, border_radius=8, height=1, opacity=0,
                  border=ft.InputBorder.NONE, filled=False)
        try:
            return ft.TextField(autocorrect=False, enable_suggestions=False, **kw)
        except TypeError:
            return ft.TextField(**kw)

    def _keys(self) -> ft.Control:
        ctl = self.ctl

        def k(label: str, fn) -> ft.Control:
            return ft.TextButton(label, height=44, on_click=lambda _: fn())

        def t(label: str, data: str) -> ft.Control:
            return k(label, lambda: ctl.send(data))

        row1 = [k("Esc", lambda: ctl.send_key("esc")), k("Tab", lambda: ctl.send_key("tab")), self.ctrl_btn,
                k("↑", lambda: ctl.send_key("up")), k("↓", lambda: ctl.send_key("down")),
                k("←", lambda: ctl.send_key("left")), k("→", lambda: ctl.send_key("right")),
                k("Home", lambda: ctl.send_key("home")), k("End", lambda: ctl.send_key("end")),
                k("PgUp", lambda: ctl.send_key("pgup")), k("PgDn", lambda: ctl.send_key("pgdn")),
                t("^C", "\x03"), t("^D", "\x04"), t("^Z", "\x1a")]
        row2 = [t("|", "|"), t("/", "/"), t("~", "~"), t("-", "-"), t("_", "_"), t(".", "."),
                t("\\", "\\"), t("*", "*"), t("&", "&"), t(">", ">"), t("$", "$"),
                ft.IconButton(ft.Icons.CONTENT_PASTE, tooltip="Paste", width=44, height=44,
                              on_click=lambda _: self.page.run_task(ctl.paste)),
                ft.IconButton(ft.Icons.COPY, tooltip="Copy output", width=44, height=44,
                              on_click=lambda _: self.page.run_task(ctl.copy_output)),
                ft.IconButton(ft.Icons.SMART_TOY, tooltip="Ask AI about this output", width=44, height=44,
                              on_click=lambda _: ctl.ask_ai_about_output()),
                ft.IconButton(ft.Icons.HUB, tooltip="Persistent sessions (tmux)", width=44, height=44,
                              on_click=lambda _: self.page.run_task(ctl.open_sessions_dialog)),
                ft.IconButton(ft.Icons.SEARCH, tooltip="Find in output", width=44, height=44,
                              on_click=lambda _: self._toggle_find()),
                ft.IconButton(ft.Icons.CLEAR_ALL, tooltip="Clear", width=44, height=44,
                              on_click=lambda _: ctl.clear()),
                ft.IconButton(ft.Icons.TEXT_DECREASE, tooltip="Smaller", width=44, height=44,
                              on_click=lambda _: ctl.change_font(-1)),
                ft.IconButton(ft.Icons.TEXT_INCREASE, tooltip="Larger", width=44, height=44,
                              on_click=lambda _: ctl.change_font(+1))]
        # Phase 15: function key row (F1-F12) for mobile + desktop without hardware keyboard.
        row3 = [k(f"F{i}", lambda i=i: ctl.send_key(f"f{i}")) for i in range(1, 13)]
        return ft.Column([
            ft.Row(row1, scroll=ft.ScrollMode.AUTO, spacing=0),
            ft.Row(row2, scroll=ft.ScrollMode.AUTO, spacing=0),
            ft.Row(row3, scroll=ft.ScrollMode.AUTO, spacing=0),
        ], spacing=0)

    # ---------------------------------------------------------------- persistent sessions
    def show_sessions_dialog(self) -> None:
        """Render the tmux sessions dialog (list / create / attach / kill)."""
        ctl = self.ctl
        sessions = list(ctl.tmux_sessions)
        col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        if not sessions:
            col.controls.append(ft.Text("No persistent sessions on this server yet.",
                                         size=13, opacity=0.7))
        for s in sessions:
            attached = " (attached)" if s.attached else ""
            col.controls.append(ft.Row([
                ft.Icon(ft.Icons.TERMINAL, size=20),
                ft.Column([
                    ft.Text(f"{s.name}{attached}", weight=ft.FontWeight.W_600, no_wrap=True),
                    ft.Text(f"{s.windows} window(s) · {s.created}", size=11, opacity=0.7, no_wrap=True),
                ], spacing=2, expand=True),
                ft.IconButton(ft.Icons.PLAY_ARROW, tooltip="Attach in new tab", width=44, height=44,
                              on_click=lambda _, n=s.name: self.page.run_task(ctl.new_session, n)),
                ft.IconButton(ft.Icons.DELETE, tooltip="Kill session", width=44, height=44,
                              on_click=lambda _, n=s.name: self.page.run_task(ctl.kill_tmux_session, n)),
            ], spacing=4))
        name_field = ft.TextField(hint_text="Session name (letters, digits, _, -)",
                                   dense=True, expand=True, border_radius=8)

        def create_new(_=None) -> None:
            n = (name_field.value or "").strip()
            if not n:
                toast(self.page, "Pick a session name first.", error=True)
                return
            self.page.pop_dialog()
            self.page.run_task(ctl.new_session, n)

        name_field.on_submit = create_new
        col.controls.append(ft.Divider(height=1))
        col.controls.append(ft.Text("Start a new persistent session:", size=12, opacity=0.7))
        col.controls.append(ft.Row([name_field,
                                    ft.IconButton(ft.Icons.ADD, tooltip="Create + attach", width=44, height=44,
                                                  on_click=create_new)], spacing=4))
        col.controls.append(ft.Text("Tip: detach inside the session with Ctrl-B then D; "
                                     "the session keeps running on the server.", size=11, opacity=0.6))
        dlg = ft.AlertDialog(
            title=ft.Text("Persistent sessions (tmux)"), modal=True,
            content=ft.Container(width=420, height=400, content=col),
            actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())])
        self.page.show_dialog(dlg)

    def refresh_sessions_dialog(self) -> None:
        """Rebuild the dialog after a session is killed or attached."""
        # Close the current dialog (if any) and reopen with fresh data.
        try:
            self.page.pop_dialog()
        except Exception:  # noqa: BLE001
            pass
        self.show_sessions_dialog()

    def refresh_ctrl(self) -> None:
        self.ctrl_btn.content = "Ctrl ●" if self.ctl.ctrl_armed else "Ctrl"

    # ---------------------------------------------------------------- input handling
    def _reset_input(self) -> None:
        self.input.value = SENTINEL

    def _on_input(self, e) -> None:
        """Phase 15: the sentinel TextField is now hidden (opacity=0) and only
        used for mobile soft-keyboard input. On desktop, on_keyboard_event
        handles everything directly. This handler is kept for backward compat
        (mobile) but avoids unnecessary page.update() calls."""
        v = e.control.value or ""
        if v == SENTINEL:
            return
        if SENTINEL not in v:                          # the sentinel was deleted → Backspace
            self.ctl.send_key("backspace")
        else:
            text = v.replace(SENTINEL, "").replace("\n", "\r")
            if text:
                self.ctl.send_text(text)
        self._reset_input()
        # Skip page.update() — the sentinel field is invisible and doesn't
        # need a visual refresh. This reduces input lag significantly.

    def _on_submit(self, e) -> None:
        self.ctl.send_key("enter")
        self._reset_input()
        # Phase 15: skip refocus + page.update on desktop — the keyboard
        # handler handles Enter directly. The refocus is only needed for
        # mobile soft-keyboard input.
        pass

    async def _refocus(self) -> None:
        try:
            await self.input.focus()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- rendering
    def attach(self) -> None:
        """(Re)bind to the active session: rebuild tabs, scrollback and screen rows."""
        ctl = self.ctl
        chips = []
        for i, s in enumerate(ctl.sessions):
            sel = i == ctl.active
            chips.append(ft.Container(
                height=40, padding=ft.Padding.only(left=10, right=2), border_radius=theme.RADIUS, ink=True,
                bgcolor=ft.Colors.PRIMARY_CONTAINER if sel else ft.Colors.SURFACE_CONTAINER_HIGH,
                on_click=lambda _, i=i: ctl.select(i),
                content=ft.Row([ft.Text(s.name + ("" if s.alive else " ✕"), size=13, no_wrap=True),
                                ft.IconButton(ft.Icons.CLOSE, icon_size=16, width=36, height=36,
                                              on_click=lambda _, i=i: ctl.close_session(i))], spacing=2)))
        chips.append(ft.IconButton(ft.Icons.ADD, tooltip="New terminal", width=44, height=44,
                                   on_click=lambda _: self.page.run_task(ctl.new_session)))
        self.tabs.controls = chips

        s = ctl.session
        self._sigs, self._scroll_ctrls = [], []
        if s is None:
            msg = ("Not connected. Connect to a server to open a terminal."
                   if ctl.ctx.ssh.status.value != "connected" else "Starting shell…")
            self.out.controls = [ft.Text(msg, color=FG, opacity=0.7)]
            return
        scr = s.screen
        fs = ctl.s.terminal_font_size
        self._rows = [ft.Text(spans=[ft.TextSpan(" ")], size=fs, font_family="monospace", color=FG,
                              no_wrap=True) for _ in range(scr.rows)]
        self._sigs = [None] * scr.rows
        self.screen_col.controls = self._rows
        self._alt = scr.alt_active
        rows = list(scr.scrollback)[-MAX_SCROLLBACK_CONTROLS:]
        self._scroll_ctrls = [self._scroll_text(r) for r in rows]
        self._seen = scr.scrolled_total
        self._layout()
        self.render(update=False)

    def _scroll_text(self, row: list) -> ft.Control:
        text = ft.Text(spans=[_span(t, st) for t, st in Screen.spans_of_row(row)],
                       size=self.ctl.s.terminal_font_size, font_family="monospace", color=FG, no_wrap=True)
        # Phase 13.7: long-press on any scrollback row opens a context menu with
        # "Send to AI" (sends this row + a few surrounding lines to the chat).
        # on_long_press is supported on ft.Container in Flet 1.0+.
        row_text = "".join(ch for ch, _ in row).rstrip()
        return ft.Container(
            content=text, on_long_press=lambda _, t=row_text: self._on_row_long_press(t),
            ink=False)

    def _on_row_long_press(self, row_text: str) -> None:
        """Phase 13.7: long-press → 'Send to AI' context menu."""
        if not row_text:
            return
        # Build a small context: the pressed row + the 5 lines around it (3 before, 1 after).
        s = self.ctl.session
        if s is None:
            return
        all_lines = s.screen.text().splitlines()
        try:
            idx = all_lines.index(row_text) if row_text in all_lines else -1
        except ValueError:
            idx = -1
        if idx >= 0:
            lo, hi = max(0, idx - 3), min(len(all_lines), idx + 2)
            ctx_lines = all_lines[lo:hi]
            # Mark the pressed line so the AI knows which one the user cares about.
            marked = ["▶ " + ln if ln == row_text else ln for ln in ctx_lines]
            payload = "\n".join(marked)
        else:
            payload = "▶ " + row_text
        prompt = ("The user long-pressed this line in the terminal and wants you to explain it "
                  "and suggest what to do next:\n\n```\n" + payload + "\n```")

        def _send(_) -> None:
            self.page.pop_dialog()
            self.ctl.ctx.open_chat(prompt)

        def _copy(_) -> None:
            self.page.pop_dialog()
            self.page.run_task(self.ctl.copy_text, row_text)

        dlg = ft.AlertDialog(
            title=ft.Text("Send to AI?", modal=True),
            content=ft.Container(width=400, content=ft.Column([
                ft.Text("Line you pressed:", size=11, opacity=0.7),
                ft.Text(row_text[:200], selectable=True, no_wrap=False, font_family="monospace", size=12),
            ], tight=True)),
            actions=[
                ft.TextButton("Copy line", on_click=_copy),
                ft.TextButton("Send to AI", on_click=_send),
                ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
            ])
        self.page.show_dialog(dlg)

    def _layout(self) -> None:
        self.out.controls = ([self.screen_col] if self._alt
                             else [*self._scroll_ctrls, self.screen_col])

    def request_render(self) -> None:
        if self._pending:
            return
        self._pending = True
        self.page.run_task(self._render_later)

    async def _render_later(self) -> None:
        await asyncio.sleep(0.06)
        self._pending = False
        if self.ctl.view is self:
            self.render()

    def render(self, update: bool = True) -> None:
        s = self.ctl.session
        if s is None:
            return
        scr = s.screen
        if len(self._rows) != scr.rows:
            self.attach()
            if update:
                self.page.update()
            return
        relayout = scr.alt_active != self._alt
        self._alt = scr.alt_active
        new = scr.scrolled_total - self._seen
        if new > 0:
            rows = list(scr.scrollback)
            fresh = rows[-min(new, len(rows)):]
            self._scroll_ctrls += [self._scroll_text(r) for r in fresh]
            del self._scroll_ctrls[:-MAX_SCROLLBACK_CONTROLS]
            self._seen = scr.scrolled_total
            relayout = True
        if relayout:
            self._layout()
        for y in range(scr.rows):
            cur = scr.cx if (scr.cursor_visible and y == scr.cy and s.alive) else None
            spans = scr.row_spans(y, cur)
            sig = tuple(spans)
            if sig != self._sigs[y]:
                self._sigs[y] = sig
                self._rows[y].spans = [_span(t, st) for t, st in spans]
        self.title.value = (scr.title or "") + ("" if s.alive else "   — session closed, tap + for a new one")
        if update:
            self.page.update()
            # Phase 13.8: only follow new output if the user was already at the bottom.
            # _maybe_scroll_to_bottom is a no-op when _at_bottom is False.
            if new > 0 or relayout:
                self._maybe_scroll_to_bottom()
