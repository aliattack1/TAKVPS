"""Terminal: a small VT100/xterm screen emulator + a PTY session. No UI code.

Supported: printable text, CR/LF/BS/TAB, cursor movement, erase, insert/delete line/char,
scroll regions, SGR (16/256/true colour, bold/italic/underline/reverse), save/restore cursor,
alternate screen (vim/less/htop/nano), application-cursor mode, cursor visibility, DSR/DA replies.

Phase 14 Part 3: wide (CJK/emoji) glyph widths are now supported — wide characters
occupy two cells, the second cell holds a `WIDE_CONT` sentinel that the renderer
skips. Combining marks (Mn/Me/Cf) attach to the previous char at zero width.
Not supported: mouse reporting, charsets, double-width lines.
"""
from __future__ import annotations

import inspect
import itertools
from collections import deque
from typing import Any, Awaitable, Callable, Optional

from services.wcwidth import WIDE_CONT, char_width, is_wide_continuation

Style = tuple          # (fg, bg, flags)   fg/bg: None | int(0-255) | "#rrggbb"
DEFAULT: Style = (None, None, 0)
BOLD, UNDER, REV, ITAL = 1, 2, 4, 8


def _palette() -> list[str]:
    base = ["#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
            "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"]
    cube = [0, 95, 135, 175, 215, 255]
    base += [f"#{cube[r]:02x}{cube[g]:02x}{cube[b]:02x}"
             for r in range(6) for g in range(6) for b in range(6)]
    base += [f"#{8 + 10 * i:02x}{8 + 10 * i:02x}{8 + 10 * i:02x}" for i in range(24)]
    return base


PALETTE = _palette()


def color_hex(c) -> Optional[str]:
    if c is None:
        return None
    return c if isinstance(c, str) else PALETTE[c] if 0 <= c < 256 else None


class Screen:
    def __init__(self, cols: int = 80, rows: int = 24, max_scrollback: int = 2000) -> None:
        self.cols, self.rows = cols, rows
        self.scrollback: deque = deque(maxlen=max_scrollback)
        self.scrolled_total = 0
        self.alt_active = False
        self._saved_main: Optional[tuple] = None
        self.grid = [self._blank_row() for _ in range(rows)]
        self.cx = self.cy = 0
        self.style: Style = DEFAULT
        self._saved_cursor = (0, 0, DEFAULT)
        self.top, self.bottom = 0, rows - 1
        self.wrap_pending = False
        self.cursor_visible = True
        self.app_cursor = False
        self.bracketed = False
        # Phase 14 Part 9: mouse reporting modes. Set by DECSET 1000/1003/1006.
        # When mouse_button or mouse_any is on AND mouse_sgr is on, the UI
        # captures mouse events and sends them to the PTY via encode_sgr().
        self.mouse_button = False     # DECSET 1000: report button presses
        self.mouse_any = False         # DECSET 1003: report presses + releases + motion
        self.mouse_sgr = False        # DECSET 1006: SGR encoding (the format we emit)
        self.title = ""
        self._responses: list[str] = []
        self._state, self._buf = "g", ""

    # ---------------------------------------------------------------- helpers
    def _blank_row(self, style: Style = DEFAULT) -> list:
        return [(" ", (None, style[1], 0))] * self.cols

    def take_responses(self) -> str:
        out, self._responses = "".join(self._responses), []
        return out

    # ---------------------------------------------------------------- mouse
    @property
    def mouse_reporting(self) -> bool:
        """Phase 14 Part 9: True when the remote app has requested mouse events.
        Requires at least one reporting mode (1000 or 1003) AND the SGR
        encoding (1006) — we don't emit legacy-format mouse events."""
        return (self.mouse_button or self.mouse_any) and self.mouse_sgr

    # ---------------------------------------------------------------- feeding
    def feed(self, text: str) -> None:
        for ch in text:
            st = self._state
            if st == "g":
                self._ground(ch)
            elif st == "esc":
                self._esc(ch)
            elif st == "csi":
                self._csi_char(ch)
            elif st == "osc":
                if ch == "\x07":
                    self._osc_done()
                elif ch == "\x1b":
                    self._state = "osc_esc"
                else:
                    self._buf += ch
                    if len(self._buf) > 512:
                        self._state = "g"
            elif st == "osc_esc":
                self._osc_done() if ch == "\\" else None
                self._state = "g"
            else:                                     # skip1
                self._state = "g"

    def _ground(self, ch: str) -> None:
        if ch == "\x1b":
            self._state = "esc"
        elif ch in "\n\x0b\x0c":
            self._lf()
        elif ch == "\r":
            self.cx, self.wrap_pending = 0, False
        elif ch == "\b":
            # Backspace: move left. If the cell to the left is a wide-char
            # continuation, skip past it to the wide char's first cell.
            self.cx = max(0, self.cx - 1)
            if self.cx > 0 and is_wide_continuation(self.grid[self.cy][self.cx][0]):
                self.cx -= 1
            self.wrap_pending = False
        elif ch == "\t":
            self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
            self.wrap_pending = False
        elif ch >= " " and ch != "\x7f":
            self._put(ch)

    def _esc(self, ch: str) -> None:
        self._state = "g"
        if ch == "[":
            self._state, self._buf = "csi", ""
        elif ch == "]":
            self._state, self._buf = "osc", ""
        elif ch in "()*+#%":
            self._state = "skip1"
        elif ch == "7":
            self._saved_cursor = (self.cx, self.cy, self.style)
        elif ch == "8":
            self.cx, self.cy, self.style = self._saved_cursor
        elif ch == "M":
            self._scroll_down(1) if self.cy == self.top else setattr(self, "cy", max(0, self.cy - 1))
        elif ch == "D":
            self._lf()
        elif ch == "E":
            self.cx = 0
            self._lf()
        elif ch == "c":
            self.__init__(self.cols, self.rows, self.scrollback.maxlen or 2000)

    def _osc_done(self) -> None:
        self._state = "g"
        num, _, rest = self._buf.partition(";")
        if num in ("0", "2"):
            self.title = rest[:120]

    def _csi_char(self, ch: str) -> None:
        if "@" <= ch <= "~":
            self._state = "g"
            self._csi(self._buf, ch)
        elif ch == "\x1b":
            self._state = "esc"
        else:
            self._buf += ch
            if len(self._buf) > 64:
                self._state = "g"

    # ---------------------------------------------------------------- primitives
    def _put(self, ch: str) -> None:
        """Write one character at the cursor.

        Phase 14 Part 3: wide chars (CJK, emoji) occupy two cells — the first
        holds the character, the second holds `WIDE_CONT`. Combining marks
        (Mn/Me/Cf, returned as width 0 by `char_width`) attach to the previous
        cell instead of advancing the cursor — that matches what real terminals
        do (the mark is rendered on top of the base glyph).
        """
        w = char_width(ch)
        if w == 0:
            # Combining mark / ZWJ / variation selector: attach to the previous
            # cell if there is one and it isn't itself a continuation. We store
            # the composed form so the renderer can show the base+mark together.
            if self.cx > 0:
                prev_ch, prev_st = self.grid[self.cy][self.cx - 1]
                if not is_wide_continuation(prev_ch) and prev_ch != " ":
                    # Compose: append the combining char to the base's cell.
                    self.grid[self.cy][self.cx - 1] = (prev_ch + ch, prev_st)
            return
        if self.wrap_pending:
            self.cx, self.wrap_pending = 0, False
            self._lf()
        # Phase 14 Part 3: a wide character needs TWO consecutive cells. If we're
        # at the last column, there isn't room — wrap to the next line first
        # (real terminals do the same; otherwise the wide glyph would render in
        # just one cell and misalign everything after it).
        if w == 2 and self.cx >= self.cols - 1:
            self.cx, self.wrap_pending = 0, False
            self._lf()
        # If we're about to overwrite a wide char's first cell, clear its
        # continuation cell too so we don't leave a dangling sentinel.
        # Likewise, if we're writing onto a continuation cell, clear the
        # matching first cell.
        if self.cx < self.cols - 1:
            cur = self.grid[self.cy][self.cx]
            nxt = self.grid[self.cy][self.cx + 1]
            if is_wide_continuation(nxt[0]):
                # We're overwriting a wide char's first cell — clear the cont.
                self.grid[self.cy][self.cx + 1] = (" ", DEFAULT)
            if is_wide_continuation(cur[0]) and self.cx > 0:
                # We're at a continuation cell; clear its first cell too.
                self.grid[self.cy][self.cx - 1] = (" ", DEFAULT)
        elif is_wide_continuation(self.grid[self.cy][self.cx][0]) and self.cx > 0:
            # Edge case: continuation cell at the last column.
            self.grid[self.cy][self.cx - 1] = (" ", DEFAULT)

        self.grid[self.cy][self.cx] = (ch, self.style)
        if w == 2:
            # Wide char: also mark the next cell as the continuation. We already
            # wrapped above if we were at the last column, so cx < cols-1 here.
            self.grid[self.cy][self.cx + 1] = (WIDE_CONT, self.style)
            if self.cx + 1 >= self.cols - 1:
                self.wrap_pending = True
            else:
                self.cx += 2
        else:
            # Narrow char: standard one-cell advance.
            if self.cx >= self.cols - 1:
                self.wrap_pending = True
            else:
                self.cx += 1

    def _lf(self) -> None:
        if self.cy == self.bottom:
            self._scroll_up(1)
        elif self.cy < self.rows - 1:
            self.cy += 1

    def _scroll_up(self, n: int) -> None:
        for _ in range(n):
            row = self.grid.pop(self.top)
            if self.top == 0 and self.bottom == self.rows - 1 and not self.alt_active:
                self.scrollback.append(row)
                self.scrolled_total += 1
            self.grid.insert(self.bottom, self._blank_row(self.style))

    def _scroll_down(self, n: int) -> None:
        for _ in range(n):
            self.grid.pop(self.bottom)
            self.grid.insert(self.top, self._blank_row(self.style))

    # ---------------------------------------------------------------- CSI
    def _csi(self, buf: str, final: str) -> None:
        private = buf[:1] in "?><=!" and buf != ""
        body = buf.lstrip("?><=!").rstrip(" !\"#$%&'()*+,-./")
        params = [int(x) if x.isdigit() else 0 for x in body.split(";")] if body else []

        def p(i: int, d: int = 1) -> int:
            return params[i] if i < len(params) and params[i] > 0 else d

        c, r = self.cols, self.rows
        if final in "ABCDEFGHfdI" and not private:
            self.wrap_pending = False
        if final == "A":
            self.cy = max(self.top if self.cy >= self.top else 0, self.cy - p(0))
        elif final == "B":
            self.cy = min(self.bottom if self.cy <= self.bottom else r - 1, self.cy + p(0))
        elif final == "C":
            self.cx = min(c - 1, self.cx + p(0))
        elif final == "D":
            self.cx = max(0, self.cx - p(0))
        elif final == "E":
            self.cx, self.cy = 0, min(r - 1, self.cy + p(0))
        elif final == "F":
            self.cx, self.cy = 0, max(0, self.cy - p(0))
        elif final == "G":
            self.cx = min(c - 1, p(0) - 1)
        elif final in "Hf":
            self.cy, self.cx = min(r - 1, p(0) - 1), min(c - 1, p(1) - 1)
        elif final == "d":
            self.cy = min(r - 1, p(0) - 1)
        elif final == "J":
            self._erase_display(params[0] if params else 0)
        elif final == "K":
            self._erase_line(params[0] if params else 0)
        elif final == "L" and self.top <= self.cy <= self.bottom:
            for _ in range(min(p(0), self.bottom - self.cy + 1)):
                self.grid.insert(self.cy, self._blank_row(self.style))
                self.grid.pop(self.bottom + 1)
        elif final == "M" and self.top <= self.cy <= self.bottom:
            for _ in range(min(p(0), self.bottom - self.cy + 1)):
                self.grid.pop(self.cy)
                self.grid.insert(self.bottom, self._blank_row(self.style))
        elif final == "P":
            row, n = self.grid[self.cy], min(p(0), c - self.cx)
            del row[self.cx:self.cx + n]
            row.extend([(" ", DEFAULT)] * n)
        elif final == "@":
            row, n = self.grid[self.cy], min(p(0), c - self.cx)
            row[self.cx:self.cx] = [(" ", DEFAULT)] * n
            del row[c:]
        elif final == "X":
            for i in range(self.cx, min(c, self.cx + p(0))):
                self.grid[self.cy][i] = (" ", DEFAULT)
        elif final == "S":
            self._scroll_up(p(0))
        elif final == "T":
            self._scroll_down(p(0))
        elif final == "m" and not private:
            self._sgr(params or [0])
        elif final == "r" and not private:
            t, b = p(0) - 1, (params[1] if len(params) > 1 and params[1] > 0 else r) - 1
            if 0 <= t < b < r:
                self.top, self.bottom = t, b
                self.cx = self.cy = 0
        elif final == "s" and not private:
            self._saved_cursor = (self.cx, self.cy, self.style)
        elif final == "u" and not private:
            self.cx, self.cy, self.style = self._saved_cursor
        elif final in "hl" and private:
            for n in params:
                self._mode(n, final == "h")
        elif final == "n" and not private:
            if params and params[0] == 6:
                self._responses.append(f"\x1b[{self.cy + 1};{self.cx + 1}R")
            elif params and params[0] == 5:
                self._responses.append("\x1b[0n")
        elif final == "c" and not private and (not params or params[0] == 0):
            self._responses.append("\x1b[?1;2c")

    def _mode(self, n: int, on: bool) -> None:
        if n == 1:
            self.app_cursor = on
        elif n == 25:
            self.cursor_visible = on
        elif n == 2004:
            self.bracketed = on
        elif n in (47, 1047, 1049):
            if on and not self.alt_active:
                self._saved_main = (self.grid, self.cx, self.cy, self.style, self.top, self.bottom)
                self.grid = [self._blank_row() for _ in range(self.rows)]
                self.alt_active, self.cx, self.cy = True, 0, 0
                self.top, self.bottom = 0, self.rows - 1
            elif not on and self.alt_active and self._saved_main:
                self.grid, self.cx, self.cy, self.style, self.top, self.bottom = self._saved_main
                self.alt_active, self._saved_main = False, None
            self.wrap_pending = False
        # Phase 14 Part 9: mouse reporting modes.
        elif n == 1000:
            self.mouse_button = on
        elif n == 1003:
            self.mouse_any = on
        elif n == 1006:
            self.mouse_sgr = on
        elif n == 1015:
            # urxvt encoding — we accept the mode flag but always emit SGR.
            pass

    def _erase_display(self, mode: int) -> None:
        if mode == 0:
            self._erase_line(0)
            for y in range(self.cy + 1, self.rows):
                self.grid[y] = self._blank_row(self.style)
        elif mode == 1:
            for y in range(self.cy):
                self.grid[y] = self._blank_row(self.style)
            self._erase_line(1)
        else:
            for y in range(self.rows):
                self.grid[y] = self._blank_row(self.style)
            if mode == 3:
                self.scrollback.clear()

    def _erase_line(self, mode: int) -> None:
        blank = (" ", (None, self.style[1], 0))
        row = self.grid[self.cy]
        a, b = (self.cx, self.cols) if mode == 0 else (0, self.cx + 1) if mode == 1 else (0, self.cols)
        # Phase 14 Part 3: when erasing cells, also clear the *partner* cell of
        # any wide character we touch, so we don't leave a dangling half-glyph.
        # Detect pairs in the original row first, then blank cell + partner.
        for i in range(a, min(b, self.cols)):
            partner = -1
            if i + 1 < self.cols and is_wide_continuation(row[i + 1][0]):
                partner = i + 1                       # this cell is the wide first half
            elif i > 0 and (not is_wide_continuation(row[i][0])
                            and row[i][0] != " "
                            and char_width(row[i][0]) == 2):
                # Cell i is itself a wide first half whose continuation is at i+1.
                # (Already covered by the branch above when i+1 is in range.)
                pass
            row[i] = blank
            if partner >= 0:
                row[partner] = blank
        # If the FIRST cell of the range was a wide-char continuation (i.e. its
        # first half is just to the left, at index a-1), clear that half too so
        # we don't leave a wide glyph with a blank partner.
        if a > 0:
            left = row[a - 1]
            if (not is_wide_continuation(left[0]) and left[0] != " "
                    and char_width(left[0]) == 2):
                row[a - 1] = blank

    def _sgr(self, params: list[int]) -> None:
        fg, bg, fl = self.style
        i = 0
        while i < len(params):
            n = params[i]
            if n == 0:
                fg, bg, fl = None, None, 0
            elif n == 1:
                fl |= BOLD
            elif n == 3:
                fl |= ITAL
            elif n == 4:
                fl |= UNDER
            elif n == 7:
                fl |= REV
            elif n == 22:
                fl &= ~BOLD
            elif n == 23:
                fl &= ~ITAL
            elif n == 24:
                fl &= ~UNDER
            elif n == 27:
                fl &= ~REV
            elif 30 <= n <= 37:
                fg = n - 30
            elif 40 <= n <= 47:
                bg = n - 40
            elif 90 <= n <= 97:
                fg = n - 90 + 8
            elif 100 <= n <= 107:
                bg = n - 100 + 8
            elif n == 39:
                fg = None
            elif n == 49:
                bg = None
            elif n in (38, 48):
                color = None
                if i + 2 < len(params) and params[i + 1] == 5:
                    color, i = params[i + 2] % 256, i + 2
                elif i + 4 < len(params) and params[i + 1] == 2:
                    r, g, b = (min(255, v) for v in params[i + 2:i + 5])
                    color, i = f"#{r:02x}{g:02x}{b:02x}", i + 4
                if color is not None:
                    fg, bg = (color, bg) if n == 38 else (fg, color)
            i += 1
        self.style = (fg, bg, fl)

    # ---------------------------------------------------------------- resize / output
    @staticmethod
    def _fit(row: list, cols: int) -> list:
        """Truncate or pad a row to exactly `cols` cells.

        Phase 14 Part 3: if the cut would split a wide character (i.e. its first
        cell survives but its continuation cell is dropped), replace the orphaned
        first cell with a blank so we don't render a wide glyph in 1 cell.
        """
        out = list(row[:cols])
        # Pad with blanks if the row was shorter than cols.
        if len(out) < cols:
            out += [(" ", DEFAULT)] * (cols - len(out))
        # Fix the boundary: if the last cell of the truncated row is a wide
        # first-half (its continuation would have been at index cols), drop it.
        if out:
            last_ch, last_st = out[-1]
            if (not is_wide_continuation(last_ch) and last_ch != " "
                    and char_width(last_ch) == 2):
                out[-1] = (" ", DEFAULT)
        # Fix the start: if the first cell is a wide continuation (its first
        # half was at index -1, dropped by the slice), drop it too.
        if out and is_wide_continuation(out[0][0]):
            out[0] = (" ", DEFAULT)
        return out

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(10, cols), max(4, rows)
        if (cols, rows) == (self.cols, self.rows):
            return

        def fit_grid(grid: list, cy: int, keep_history: bool) -> tuple[list, int]:
            grid = [self._fit(r, cols) for r in grid]
            while len(grid) > rows:
                if cy > 0:
                    row = grid.pop(0)
                    if keep_history:
                        self.scrollback.append(row)
                        self.scrolled_total += 1
                    cy -= 1
                else:
                    grid.pop()
            while len(grid) < rows:
                grid.append([(" ", DEFAULT)] * cols)
            return grid, cy

        self.cols, self.rows = cols, rows
        self.grid, self.cy = fit_grid(self.grid, self.cy, not self.alt_active)
        if self._saved_main:
            g, cx, cy, st, _t, _b = self._saved_main
            g, cy = fit_grid(g, cy, True)
            self._saved_main = (g, min(cx, cols - 1), cy, st, 0, rows - 1)
        self.cx = min(self.cx, cols - 1)
        self.top, self.bottom, self.wrap_pending = 0, rows - 1, False

    def row_spans(self, y: int, cursor_x: Optional[int] = None) -> list[tuple[str, Style]]:
        spans: list[tuple[str, Style]] = []
        for x, (ch, st) in enumerate(self.grid[y]):
            # Phase 14 Part 3: skip wide-char continuation cells — the wide
            # char itself was already emitted in the previous cell.
            if is_wide_continuation(ch):
                continue
            if x == cursor_x:
                st = (st[0], st[1], st[2] ^ REV)
            if spans and spans[-1][1] == st:
                spans[-1] = (spans[-1][0] + ch, st)
            else:
                spans.append((ch, st))
        return spans

    @staticmethod
    def spans_of_row(row: list) -> list[tuple[str, Style]]:
        out: list[tuple[str, Style]] = []
        for ch, st in row:
            if is_wide_continuation(ch):
                continue
            if out and out[-1][1] == st:
                out[-1] = (out[-1][0] + ch, st)
            else:
                out.append((ch, st))
        return out

    def text(self) -> str:
        rows = [] if self.alt_active else [r for r in self.scrollback]
        rows += self.grid
        # Phase 14 Part 3: strip continuation cells so copy/paste gives just the
        # wide char, no gap. Combining marks are already merged into their base
        # cell's string by `_put`, so they ride along here.
        lines = ["".join(ch for ch, _ in r if not is_wide_continuation(ch)).rstrip()
                 for r in rows]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)


# ---------------------------------------------------------------------- keys
def key_sequence(name: str, app_cursor: bool = False) -> str:
    o = "\x1bO" if app_cursor else "\x1b["
    return {"up": o + "A", "down": o + "B", "right": o + "C", "left": o + "D",
            "home": o + "H", "end": o + "F", "pgup": "\x1b[5~", "pgdn": "\x1b[6~",
            "esc": "\x1b", "tab": "\t", "shift_tab": "\x1b[Z", "del": "\x1b[3~",
            "enter": "\r", "backspace": "\x7f",
            "insert": "\x1b[2~",
            "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS",
            "f5": "\x1b[15~", "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~",
            "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
            }.get(name, "")


def ctrl_char(ch: str) -> Optional[str]:
    """Control code for Ctrl+<ch>, or None."""
    if len(ch) == 1 and ch.isalpha() and ch.isascii():
        return chr(ord(ch.upper()) - 64)
    return {"[": "\x1b", "\\": "\x1c", "]": "\x1d", " ": "\x00", "@": "\x00"}.get(ch)


# ---------------------------------------------------------------------- session
class TerminalSession:
    """One PTY shell + its screen. `spawn(fn)` runs a coroutine function on the app loop."""
    _ids = itertools.count(1)

    def __init__(self, ssh: Any, spawn: Callable[[Callable[[], Awaitable]], Any], cols: int, rows: int,
                 on_update: Callable[["TerminalSession"], Any], command: Optional[str] = None) -> None:
        self.id = next(self._ids)
        self.name = f"Terminal {self.id}"
        self.ssh, self._spawn, self.on_update, self.command = ssh, spawn, on_update, command
        self.screen = Screen(cols, rows)
        self.shell: Any = None
        self.alive = False

    async def start(self) -> None:
        self.shell = await self.ssh.open_shell(self.screen.cols, self.screen.rows, self.command)
        self.alive = True
        self._spawn(self._loop)

    def _changed(self) -> None:
        res = self.on_update(self)
        if inspect.isawaitable(res):
            import asyncio
            asyncio.ensure_future(res)

    async def _loop(self) -> None:
        try:
            while True:
                data = await self.shell.read(4096)
                if not data:
                    break
                self.screen.feed(data)
                reply = self.screen.take_responses()
                if reply:
                    self.shell.write(reply)
                self._changed()
        except Exception as e:  # noqa: BLE001
            self.screen.feed(f"\r\n[error: {getattr(e, 'message', None) or e}]")
        finally:
            self.alive = False
            self.screen.feed("\r\n\x1b[2m[session closed]\x1b[0m\r\n")
            self._changed()

    def write(self, data: str) -> None:
        if self.alive and data:
            try:
                self.shell.write(data)
            except Exception:  # noqa: BLE001
                pass

    def resize(self, cols: int, rows: int) -> None:
        if (cols, rows) == (self.screen.cols, self.screen.rows):
            return
        self.screen.resize(cols, rows)
        if self.alive:
            try:
                self.shell.resize(self.screen.cols, self.screen.rows)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        if self.shell:
            try:
                self.shell.close()
            except Exception:  # noqa: BLE001
                pass
        self.alive = False
