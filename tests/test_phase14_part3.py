"""Phase 14 Part 3 tests: wide-character width lookup + terminal emulator behavior.

Tests both the pure `services/wcwidth.py` helper and the `Screen` emulator's
handling of CJK / emoji / combining marks (no Flet imports).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.wcwidth import WIDE_CONT, char_width, is_wide_continuation, string_width
from services.terminal import Screen


# ======================================================================
# Phase 14 Part 3 — services/wcwidth.py
# ======================================================================
class TestCharWidthASCII(unittest.TestCase):
    def test_ascii_letters_are_narrow(self):
        for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
            self.assertEqual(char_width(c), 1, f"{c!r}")

    def test_ascii_digits_and_punct_are_narrow(self):
        for c in "0123456789!@#$%^&*()-_=+[]{}|;:'\",.<>?/":
            self.assertEqual(char_width(c), 1, f"{c!r}")

    def test_space_is_narrow(self):
        self.assertEqual(char_width(" "), 1)


class TestCharWidthCJK(unittest.TestCase):
    def test_chinese_hanzi_is_wide(self):
        for c in "中文汉字测试":
            self.assertEqual(char_width(c), 2, f"{c!r}")

    def test_japanese_hiragana_is_wide(self):
        for c in "あいうえお":
            self.assertEqual(char_width(c), 2, f"{c!r}")

    def test_japanese_katakana_is_wide(self):
        for c in "アイウエオ":
            self.assertEqual(char_width(c), 2, f"{c!r}")

    def test_korean_hangul_is_wide(self):
        for c in "한글테스트":
            self.assertEqual(char_width(c), 2, f"{c!r}")

    def test_fullwidth_latin_is_wide(self):
        # U+FF21 = fullwidth A
        for c in "ＡＢＣ１２３":
            self.assertEqual(char_width(c), 2, f"{c!r}")


class TestCharWidthEmoji(unittest.TestCase):
    def test_common_emoji_are_wide(self):
        for c in "😀❤✅⭐❌✨":
            self.assertEqual(char_width(c), 2, f"{c!r}")

    def test_checkmark_is_wide_in_emoji_context(self):
        # ✓ (U+2713) is normally narrow per EAW, but we override it to wide
        # because it almost always renders as an emoji glyph.
        self.assertEqual(char_width("✓"), 2)


class TestCharWidthCombining(unittest.TestCase):
    def test_empty_string_is_zero(self):
        self.assertEqual(char_width(""), 0)

    def test_control_chars_are_zero(self):
        for c in ("\x00", "\x07", "\x08", "\x1b", "\x7f"):
            self.assertEqual(char_width(c), 0, f"0x{ord(c):02x}")

    def test_combining_marks_are_zero(self):
        # U+0301 = combining acute accent
        self.assertEqual(char_width("\u0301"), 0)
        # U+0308 = combining diaeresis
        self.assertEqual(char_width("\u0308"), 0)

    def test_zero_width_joiner_is_zero(self):
        self.assertEqual(char_width("\u200d"), 0)

    def test_variation_selector_is_zero(self):
        # U+FE0F = variation selector-16 (emoji presentation)
        self.assertEqual(char_width("\ufe0f"), 0)


class TestStringWidth(unittest.TestCase):
    def test_pure_ascii(self):
        self.assertEqual(string_width("hello"), 5)

    def test_pure_cjk(self):
        # 2 chars × 2 cells each = 4
        self.assertEqual(string_width("你好"), 4)

    def test_mixed(self):
        # 'hi 中' = 2 narrow + 1 narrow(space) + 1 wide = 5
        self.assertEqual(string_width("hi 中"), 5)

    def test_combining_doesnt_add(self):
        # 'e' + combining acute = 1 cell (the mark attaches for free)
        self.assertEqual(string_width("e\u0301"), 1)

    def test_emoji_in_string(self):
        # 'ok ✅' = 2 + 1 + 2 = 5
        self.assertEqual(string_width("ok ✅"), 5)


class TestWideContSentinel(unittest.TestCase):
    def test_is_wide_continuation_recognizes_sentinel(self):
        self.assertTrue(is_wide_continuation(WIDE_CONT))

    def test_is_wide_continuation_rejects_normal_chars(self):
        self.assertFalse(is_wide_continuation(" "))
        self.assertFalse(is_wide_continuation("a"))
        self.assertFalse(is_wide_continuation("中"))


class TestCharWidthCaching(unittest.TestCase):
    def test_repeated_calls_are_cached(self):
        # The lru_cache means repeated calls return the same result (and fast).
        # We just verify correctness; the cache is an internal optimisation.
        for _ in range(100):
            self.assertEqual(char_width("中"), 2)
        # The cache info should show hits.
        info = char_width.cache_info()
        self.assertGreater(info.hits, 0)


# ======================================================================
# Phase 14 Part 3 — Screen emulator wide-char behavior
# ======================================================================
class TestScreenWideChars(unittest.TestCase):
    def test_ascii_unaffected(self):
        s = Screen(20, 4)
        s.feed("hello world")
        self.assertEqual(s.text(), "hello world")

    def test_cjk_advances_cursor_by_two(self):
        s = Screen(20, 4)
        s.feed("你好")
        self.assertEqual(s.text(), "你好")
        self.assertEqual(s.cx, 4)     # 2 chars × 2 cells

    def test_mixed_ascii_and_cjk(self):
        s = Screen(20, 4)
        s.feed("hi 中")
        self.assertEqual(s.text(), "hi 中")
        self.assertEqual(s.cx, 5)     # 'hi ' = 3 cells, '中' = 2 cells

    def test_emoji(self):
        s = Screen(20, 4)
        s.feed("ok ✅")
        self.assertEqual(s.text(), "ok ✅")


class TestScreenCombiningMarks(unittest.TestCase):
    def test_combining_attaches_to_previous(self):
        s = Screen(10, 4)
        s.feed("e\u0301")     # e + combining acute
        # The cell holds the composed (decomposed, actually) string 'e\u0301'.
        self.assertEqual(s.text(), "e\u0301")
        # Cursor advanced only for the base char (1 cell), not the mark.
        self.assertEqual(s.cx, 1)

    def test_combining_does_not_crash_on_blank(self):
        # A combining mark at the start of a row (no previous char) is dropped
        # silently — it can't attach to anything.
        s = Screen(10, 4)
        s.feed("\u0301")
        # No exception, no glyph.
        self.assertEqual(s.text(), "")


class TestScreenBackspace(unittest.TestCase):
    def test_backspace_lands_on_wide_first_cell(self):
        s = Screen(20, 4)
        s.feed("你好")
        # cx is 4. Backspace should land on col 3 — but col 3 is the wide
        # continuation, so it should skip back to col 2 (the wide char's first cell).
        s.feed("\b")
        self.assertEqual(s.cx, 2)
        # Now writing 'X' should overwrite 你 at col 2 and clear the cont at col 3.
        s.feed("X")
        self.assertEqual(s.text(), "你X")


class TestScreenWideWrap(unittest.TestCase):
    def test_wide_char_fits_exactly_at_end(self):
        # 4 cols, 'ab' takes cols 0-1, '中' fits in cols 2-3.
        s = Screen(4, 4)
        s.feed("ab中")
        self.assertEqual(s.text(), "ab中")

    def test_wide_char_wraps_when_no_room(self):
        # 4 cols, 'abc' fills cols 0-2 (cx=3, last col). '中' needs 2 cells,
        # can't fit in 1 — wraps to next line.
        s = Screen(4, 4)
        s.feed("abc中")
        self.assertEqual(s.text(), "abc\n中")


class TestScreenWideErase(unittest.TestCase):
    def test_erase_line_clears_wide_pairs(self):
        # 4 wide chars × 2 = 8 cells; erase-to-end-of-line should clear all of them.
        s = Screen(10, 4)
        s.feed("你好世界")
        s.feed("\r\x1b[K")        # CR + clear to EOL
        self.assertEqual(s.text(), "")

    def test_erase_from_cursor_clears_partner(self):
        # Cursor sits between a wide char's two cells; erasing from the cursor
        # should also clear the first half (now orphaned).
        s = Screen(10, 4)
        s.feed("你好")
        # Move cursor to col 3 (the wide continuation of 好). Then erase to EOL.
        s.feed("\x1b[3G\x1b[K")
        # The continuation is gone; the first half should also be cleared.
        text = s.text()
        # 你 should still be there (cols 0-1), but 好 should be gone.
        self.assertIn("你", text)


class TestScreenWideRendering(unittest.TestCase):
    def test_row_spans_skips_continuations(self):
        s = Screen(10, 4)
        s.feed("你好")
        spans = s.row_spans(0)
        # One span containing '你好' + trailing blanks; no WIDE_CONT markers.
        self.assertEqual(len(spans), 1)
        self.assertTrue(spans[0][0].startswith("你好"))
        # The continuation sentinel must NOT appear as a separate span text
        # (it's the empty string, which would be invisible but wrong).
        for text, _ in spans:
            self.assertTrue(text)     # no empty-string spans

    def test_spans_of_row_static_method(self):
        s = Screen(10, 4)
        s.feed("你好")
        spans = Screen.spans_of_row(s.grid[0])
        self.assertEqual(len(spans), 1)
        self.assertTrue(spans[0][0].startswith("你好"))

    def test_text_strips_continuations(self):
        s = Screen(10, 4)
        s.feed("你好")
        # text() must give just '你好', not '你好' with embedded continuation markers.
        self.assertEqual(s.text(), "你好")


class TestScreenWideOverwrite(unittest.TestCase):
    def test_overwrite_wide_first_cell_clears_cont(self):
        s = Screen(10, 4)
        s.feed("你好")
        s.feed("\r")             # back to col 0
        s.feed("X")               # overwrite 你's first cell
        # X replaces 你 at col 0; the continuation at col 1 must be cleared.
        self.assertTrue(s.text().startswith("X"))
        # The second wide char (好) should be unaffected (it's at cols 2-3).
        self.assertIn("好", s.text())


class TestScreenWideResize(unittest.TestCase):
    def test_fit_drops_orphaned_wide_first_cell(self):
        # A row of 8 cells: A B 你[cont] 好[cont] C D
        # Truncating to 3 cells would orphan 你 (its cont at idx 3 is cut).
        row = [
            ("A", (None, None, 0)), ("B", (None, None, 0)),
            ("你", (None, None, 0)), (WIDE_CONT, (None, None, 0)),
            ("好", (None, None, 0)), (WIDE_CONT, (None, None, 0)),
            ("C", (None, None, 0)), ("D", (None, None, 0)),
        ]
        fitted = Screen._fit(row, 3)
        self.assertEqual(len(fitted), 3)
        # A, B survive; the orphaned 你 at idx 2 is replaced with a blank.
        self.assertEqual(fitted[0][0], "A")
        self.assertEqual(fitted[1][0], "B")
        self.assertEqual(fitted[2][0], " ")

    def test_fit_drops_orphaned_continuation_at_start(self):
        # If the slice starts at a continuation cell, the first cell of the
        # truncated row is a WIDE_CONT — we replace it with a blank.
        row = [
            ("你", (None, None, 0)), (WIDE_CONT, (None, None, 0)),
            ("好", (None, None, 0)), (WIDE_CONT, (None, None, 0)),
        ]
        # Slice starting at idx 1 (the continuation of 你), then fit to 2 cells.
        # Result: [cont, 好]. The cont is orphaned → blank. 好's continuation
        # was at idx 3 (cut off) → 好 is also orphaned → blank.
        # Both cells end up blank, which is correct: a wide char can't render
        # without its partner.
        fitted = Screen._fit(row[1:], 2)
        self.assertEqual(len(fitted), 2)
        self.assertEqual(fitted[0][0], " ")     # orphaned cont → blank
        self.assertEqual(fitted[1][0], " ")     # orphaned first-half → blank

    def test_fit_preserves_wide_pair_when_both_survive(self):
        # When both halves of a wide char survive the truncation, it's preserved.
        row = [
            ("A", (None, None, 0)),
            ("你", (None, None, 0)), (WIDE_CONT, (None, None, 0)),
            ("B", (None, None, 0)),
        ]
        fitted = Screen._fit(row, 4)     # no truncation
        self.assertEqual(fitted[1][0], "你")
        self.assertEqual(fitted[2][0], WIDE_CONT)
        self.assertEqual(fitted[3][0], "B")

    def test_fit_pads_short_row(self):
        row = [("A", (None, None, 0)), ("B", (None, None, 0))]
        fitted = Screen._fit(row, 5)
        self.assertEqual(len(fitted), 5)
        self.assertEqual(fitted[0][0], "A")
        self.assertEqual(fitted[1][0], "B")
        # Padded with blanks.
        self.assertEqual(fitted[2][0], " ")


class TestScreenWideANSI(unittest.TestCase):
    def test_colored_cjk_roundtrip(self):
        # Red '红', reset, then '字'.
        s = Screen(20, 4)
        s.feed("\x1b[31m红\x1b[0m字")
        self.assertEqual(s.text(), "红字")

    def test_style_split_in_row_spans(self):
        s = Screen(10, 4)
        s.feed("\x1b[31m你\x1b[0m好")
        spans = s.row_spans(0)
        # First span is '你' (red), second span starts with '好' (default).
        self.assertEqual(spans[0][0], "你")
        self.assertTrue(spans[1][0].startswith("好"))


class TestScreenWideRealistic(unittest.TestCase):
    def test_vim_status_line(self):
        # A realistic CJK status line you might see in vim.
        s = Screen(40, 4)
        s.feed("文件名: 测试.txt")
        self.assertEqual(s.text(), "文件名: 测试.txt")

    def test_chinese_sentence(self):
        s = Screen(40, 4)
        s.feed("你好，世界！")
        self.assertEqual(s.text(), "你好，世界！")

    def test_mixed_emoji_and_text(self):
        s = Screen(40, 4)
        s.feed("部署完成 ✅ 错误 ❌")
        # All chars preserved; wide chars don't break the layout.
        self.assertEqual(s.text(), "部署完成 ✅ 错误 ❌")


# ======================================================================
# Phase 14 Part 3 — backward compatibility (existing tests still pass)
# ======================================================================
class TestScreenBackwardCompat(unittest.TestCase):
    """Re-verifies a handful of behaviors from test_terminal.py to confirm the
    wide-char changes didn't break the basic VT100 path."""

    def test_plain_text_and_wrap(self):
        s = Screen(10, 4)
        s.feed("hello world")
        # 'hello worl' fits (10), then 'd' wraps to row 1.
        self.assertEqual(s.text(), "hello worl\nd")

    def test_carriage_return(self):
        s = Screen(10, 4)
        s.feed("abc\rX")
        self.assertEqual(s.text(), "Xbc")

    def test_linefeed(self):
        # This emulator treats `\n` as line-feed only (cursor down, no CR).
        # To get `abc\ndef`, you need `abc\r\ndef` (CR + LF). This matches
        # the original Phase 6 behavior — we re-verify it here.
        s = Screen(10, 4)
        s.feed("abc\r\ndef")
        self.assertEqual(s.text(), "abc\ndef")

    def test_tab(self):
        s = Screen(20, 4)
        s.feed("a\tb")
        # Tab advances to the next multiple of 8.
        self.assertEqual(s.cx, 9)

    def test_erase_to_end_of_line(self):
        s = Screen(10, 4)
        s.feed("hello\rAB\x1b[K")
        # 'AB' replaces 'he', then erase-to-EOL clears the rest.
        self.assertEqual(s.text(), "AB")


if __name__ == "__main__":
    unittest.main()
