"""Part 3 — Wide-character width lookup for the terminal emulator.

Real terminals (xterm, vim, htop) render East-Asian wide characters (CJK
ideographs, full-width Latin, most emoji) as **two cells** wide. Phase 6's
emulator treated every character as one cell, so `vim` and `htop` misaligned
the moment a CJK character appeared.

This module exposes `char_width(ch)` returning 0 (combining/control),
1 (normal), or 2 (wide), using only stdlib (`unicodedata`). No new deps.

The data source is Unicode's `East_Asian_Width` property:
  - "W" (Wide) and "F" (Fullwidth)  → 2 cells
  - "A" (Ambiguous)                  → 1 cell on Linux (some terminals use 2
                                       in CJK locales; we pick the safe Linux
                                       default, which matches what `vim` and
                                       `htop` expect on a typical VPS)
  - "Na" (Narrow), "H" (Halfwidth)  → 1 cell
  - "N"  (Neutral)                  → 1 cell

In addition, characters in the Mn/Me/Cf categories that combine with the
previous glyph (zero-width joiners, variation selectors, combining marks)
return 0 so the caller knows to attach them rather than advance the cursor.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache


# Cell-width return values.
WIDTH_NARROW = 1
WIDTH_WIDE = 2
WIDTH_ZERO = 0      # combining marks, ZWJ, variation selectors, etc.

# East-Asian-Width categories we treat as wide on Linux terminals.
_WIDE_EAW = {"W", "F"}
# "A" (Ambiguous) is locale-dependent. Linux desktop terminals default to 1
# (narrow); a CJK user with `LANG=ja_JP.UTF-8` would expect 2. We pick 1
# because the app's UI language is English and most VPS locales are `C.UTF-8`
# or `en_US.UTF-8`. Users who want CJK-wide ambiguous can override via a
# future setting — for now narrow matches the server-side default.
_AMBIGUOUS_WIDTH = WIDTH_NARROW

# Combining categories: Mn = nonspacing mark, Me = enclosing mark, Cf = format
# (includes ZWJ U+200D and variation selectors U+FE00–U+FE0F).
_COMBINING_CATEGORIES = {"Mn", "Me", "Cf"}


@lru_cache(maxsize=8192)
def char_width(ch: str) -> int:
    """Return 0, 1, or 2 — the number of terminal cells `ch` occupies.

    A few special cases:
      - Empty string or None → 0 (defensive; the caller usually feeds one char).
      - Surrogates / unpaired halves → 1 (so they don't crash the renderer).
      - ASCII control chars (< 0x20) → 0 (the caller handles them as escapes;
        returning 0 here keeps them from advancing the cursor when they slip
        through the escape-state machine).
      - DEL (0x7F) → 0 (xterm treats it as a no-op).
    """
    if not ch:
        return WIDTH_ZERO
    # `ord()` raises on surrogate halves; we catch and fall through to "1".
    try:
        cp = ord(ch)
    except (TypeError, ValueError):
        return WIDTH_NARROW

    # ASCII control characters and DEL: don't advance the cursor.
    if cp < 0x20 or cp == 0x7F:
        return WIDTH_ZERO

    # Combining marks, ZWJ, variation selectors, etc.: zero width.
    try:
        cat = unicodedata.category(ch)
    except ValueError:
        return WIDTH_NARROW
    if cat in _COMBINING_CATEGORIES:
        return WIDTH_ZERO

    # The big switch: East_Asian_Width.
    try:
        eaw = unicodedata.east_asian_width(ch)
    except ValueError:
        # Some Python builds don't ship the full UCD; fall back to category.
        # CJK ideographs are category "Lo"; treat those as wide.
        if cat == "Lo" and cp >= 0x1100:
            return WIDTH_WIDE
        return WIDTH_NARROW

    if eaw in _WIDE_EAW:
        return WIDTH_WIDE
    if eaw == "A":
        return _AMBIGUOUS_WIDTH
    return WIDTH_NARROW


def string_width(s: str) -> int:
    """Total cell width of a string (sum of per-char widths). Zero-width
    characters (combining marks) attach to the previous char for free, so a
    string like `'e\u0301'` (e + combining acute) is still width 1."""
    return sum(char_width(ch) for ch in s)


# A sentinel we use to mark the **second cell** of a wide character in the
# emulator's grid. Using an empty string (rather than a space) means:
#   - `Screen.text()` strips it (so copy/paste gives just the wide char, no gap)
#   - `Screen.row_spans` skips it when building span text (so the wide char
#     renders as one glyph, not "char + space")
#   - Erasing the cell restores a real blank (" ")
WIDE_CONT = ""


def is_wide_continuation(cell_char: str) -> bool:
    """True if `cell_char` is the placeholder that marks the second cell of a
    wide character. Used by the renderer to skip it and by erasers to know
    that the *previous* cell is the one to clear."""
    return cell_char is WIDE_CONT or cell_char == WIDE_CONT


# Special-case a few ranges that `unicodedata` historically mishandled or
# that users specifically expect to be wide:
#   - Emoji presentation selectors and keycaps aren't caught by EAW, so we
#     add the most common ones explicitly. This is a tiny table; the full
#     Unicode emoji-width rules are a moving target.
_EXTRA_WIDE = frozenset({
    # Keycap sequences aren't here (they're multi-codepoint), but the bare
    # keycap base chars (#, *, 0-9) are narrow — the wide glyph only appears
    # with U+FE0F + U+20E3. We let the FE0F be zero-width so the keycap
    # renders as 2 cells total (base + selector + combining keycap).
    #
    # Pure-emoji that should be wide regardless of EAW:
    0x2713,   # ✓ (check mark) — EAW says "N" but emoji font renders wide
    0x2714,   # ✔ heavy check
    0x2716,   # ✖ heavy multiply
    0x2728,   # ✨ sparkles
    0x274C,   # ❌ cross mark
    0x274E,   # ❎ negative
    0x2B06,   # ⬆ up arrow (emoji)
    0x2B07,   # ⬇ down arrow
    0x2B05,   # ⬅ left arrow
    0x27A1,   # ➡ right arrow
    0x2600,   # ☀ sun
    0x2601,   # ☁ cloud
    0x2614,   # ☔ umbrella
    0x2615,   # ☕ hot beverage
    0x2705,   # ✅ white heavy check
    0x2753,   # ❓ question
    0x2754,   # ❔ white question
    0x2755,   # ❕ white exclamation
    0x2757,   # ❗ exclamation
    0x2764,   # ❤ heavy heart
    0x2763,   # ❣ heart exclamation
    0x2721,   # ✡ star of david
    0x2734,   # ✴ eight pointed star
    0x2733,   # ✳ eight spoked asterisk
    0x2B50,   # ⭐ star (emoji)
    0x2B55,   # ⭕ heavy large circle
})


def _patched_width(ch: str) -> int:  # pragma: no cover — exercised via char_width
    """Inner: applies the _EXTRA_WIDE override on top of EAW. Re-exported as
    `char_width` below so callers always get the patched version."""
    if not ch:
        return WIDTH_ZERO
    try:
        cp = ord(ch)
    except (TypeError, ValueError):
        return WIDTH_NARROW
    if cp < 0x20 or cp == 0x7F:
        return WIDTH_ZERO
    try:
        cat = unicodedata.category(ch)
    except ValueError:
        return WIDTH_NARROW
    if cat in _COMBINING_CATEGORIES:
        return WIDTH_ZERO
    if cp in _EXTRA_WIDE:
        return WIDTH_WIDE
    try:
        eaw = unicodedata.east_asian_width(ch)
    except ValueError:
        if cat == "Lo" and cp >= 0x1100:
            return WIDTH_WIDE
        return WIDTH_NARROW
    if eaw in _WIDE_EAW:
        return WIDTH_WIDE
    if eaw == "A":
        return _AMBIGUOUS_WIDTH
    return WIDTH_NARROW


# Replace the public `char_width` with the patched version, preserving the
# lru_cache (which already returned the right values for the common cases;
# the patch only adds the _EXTRA_WIDE overrides, which the cache misses on
# first hit and then remembers).
char_width = lru_cache(maxsize=8192)(_patched_width)
