"""Mouse reporting for the terminal emulator.

Real terminals (xterm, vim, less, tmux) can request mouse event reporting via
DEC private modes. When enabled, the terminal sends mouse clicks and
wheel-scrolls to the application as escape sequences, so the user can click
in vim or scroll in less without touching the keyboard.

This module encodes mouse events into the **SGR (1006)** format, which is the
modern standard (no 224-coordinate limit, no encoding ambiguity). The format:

    Button press:   \\x1b[<{button};{x};{y}M
    Button release: \\x1b[<{button};{x};{y}m

Where `button` encodes the button + modifiers:
    Button 1 (left):    0
    Button 2 (middle):  1
    Button 3 (right):   2
    Wheel up:            64
    Wheel down:          65
    +4 if Shift held
    +8 if Alt/Meta held
    +16 if Ctrl held

Release events add 32 to the button code (in the legacy format); SGR uses
the lowercase `m` suffix instead, which is cleaner.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Mouse reporting modes (DECSET / DECRST codes).
MODE_MOUSE_BUTTON = 1000     # report button presses only
MODE_MOUSE_ANY = 1003         # report button presses + releases + motion
MODE_MOUSE_SGR = 1006         # SGR encoding (the one we emit)
MODE_MOUSE_URXVT = 1015       # urxvt encoding (we accept but emit SGR)
# Mode 1006 is the modern SGR encoding we prefer. 1000/1003 enable reporting;
# 1006 selects the format. We always emit SGR regardless of which format the
# app requested, because SGR is unambiguous and supported by every modern
# terminal app (vim 7.4+, less 552+, tmux 2.1+).

# Button codes.
BUTTON_LEFT = 0
BUTTON_MIDDLE = 1
BUTTON_RIGHT = 2
WHEEL_UP = 64
WHEEL_DOWN = 65
# The release code in legacy encoding is button + 32, but SGR uses 'm' suffix.

# Modifiers (added to the button code).
MOD_SHIFT = 4
MOD_ALT = 8
MOD_CTRL = 16


@dataclass
class MouseEvent:
    """A single mouse event to send to the remote app.

    `button` is one of BUTTON_LEFT/MIDDLE/RIGHT or WHEEL_UP/WHEEL_DOWN.
    `x` and `y` are 1-based terminal coordinates (column, row) — matching
    what the remote app expects. The top-left cell is (1, 1).
    `release` is True for button-release events (SGR uses lowercase 'm').
    `shift`/`alt`/`ctrl` are modifier flags.
    """
    button: int
    x: int
    y: int
    release: bool = False
    shift: bool = False
    alt: bool = False
    ctrl: bool = False

    def code(self) -> int:
        """The numeric button+modifier code (before the SGR suffix)."""
        c = self.button
        if self.shift:
            c += MOD_SHIFT
        if self.alt:
            c += MOD_ALT
        if self.ctrl:
            c += MOD_CTRL
        return c

    def encode_sgr(self) -> str:
        """Encode as an SGR (1006) mouse escape sequence."""
        suffix = "m" if self.release else "M"
        return f"\x1b[<{self.code()};{self._clamp(self.x)};{self._clamp(self.y)}{suffix}"

    @staticmethod
    def _clamp(v: int, lo: int = 1, hi: int = 224) -> int:
        """Clamp to the legacy 224-coordinate range for safety (some apps
        still expect it even in SGR mode)."""
        return max(lo, min(hi, v))


def encode_click(button: int, x: int, y: int, *, release: bool = False,
                 shift: bool = False, alt: bool = False, ctrl: bool = False) -> str:
    """Convenience: encode a click event as an SGR escape sequence."""
    return MouseEvent(button, x, y, release=release, shift=shift, alt=alt, ctrl=ctrl).encode_sgr()


def encode_wheel(direction: str, x: int, y: int, *,
                 shift: bool = False, alt: bool = False, ctrl: bool = False) -> str:
    """Encode a wheel-scroll event. `direction` is "up" or "down"."""
    button = WHEEL_UP if direction == "up" else WHEEL_DOWN
    return MouseEvent(button, x, y, shift=shift, alt=alt, ctrl=ctrl).encode_sgr()


def is_mouse_mode(mode: int) -> bool:
    """True if `mode` is one of the mouse-reporting DECSET codes."""
    return mode in (MODE_MOUSE_BUTTON, MODE_MOUSE_ANY, MODE_MOUSE_SGR, MODE_MOUSE_URXVT)


def should_report(mouse_button: bool, mouse_any: bool, mouse_sgr: bool) -> bool:
    """Whether to actually send mouse events. Requires at least one of the
    reporting modes (1000 or 1003) AND the SGR encoding (1006) — we don't
    emit legacy-format mouse events."""
    return (mouse_button or mouse_any) and mouse_sgr


def flet_button_to_code(flet_button: str) -> Optional[int]:
    """Map Flet's mouse-button names to our button codes.

    Flet's `on_click` event doesn't tell us which button; `on_hover` and
    `on_click` give us the position. The caller should use this for
    right-click / middle-click detection if available; for left-click, the
    default is BUTTON_LEFT.
    """
    return {
        "left": BUTTON_LEFT,
        "middle": BUTTON_MIDDLE,
        "right": BUTTON_RIGHT,
    }.get(flet_button.lower() if flet_button else "", BUTTON_LEFT)


def parse_sgr_response(text: str) -> Optional[MouseEvent]:
    """Parse an SGR mouse response (received from the app, e.g. for a
    click-and-drag selection). Returns None if not a valid SGR sequence.

    This is for completeness — the emulator mostly sends mouse events, not
    receives them. But some apps (like tmux) can send mouse responses back.
    """
    if not text.startswith("\x1b[<"):
        return None
    body = text[3:]     # strip \x1b[<
    if not body or body[-1] not in "Mm":
        return None
    release = body[-1] == "m"
    parts = body[:-1].split(";")
    if len(parts) != 3:
        return None
    try:
        code, x, y = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    # Decode modifiers from the code.
    shift = bool(code & MOD_SHIFT)
    alt = bool(code & MOD_ALT)
    ctrl = bool(code & MOD_CTRL)
    button_code = code & ~(MOD_SHIFT | MOD_ALT | MOD_CTRL)
    return MouseEvent(button=button_code, x=x, y=y, release=release,
                       shift=shift, alt=alt, ctrl=ctrl)
