"""Phase 14 Part 6 tests: search in terminal scrollback.

Tests the pure `services/term_search.py` helper: pattern compilation, match
finding, next/prev navigation, wide-char handling, summary formatting, and
regex validation. No Flet imports.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.term_search import (Match, compile_pattern, find_matches, is_valid_regex,
                                    match_at_row, next_match, rows_to_text, search, summary)
from services.wcwidth import WIDE_CONT


# ---- test rows (lists of cells) -------------------------------------------

def row(text: str, style=(None, None, 0)) -> list:
    """Build a row of cells from a plain string."""
    return [(ch, style) for ch in text]


def wide_row(text: str) -> list:
    """Build a row with wide chars — each wide char gets a WIDE_CONT after it."""
    cells = []
    for ch in text:
        from services.wcwidth import char_width
        w = char_width(ch)
        cells.append((ch, (None, None, 0)))
        if w == 2:
            cells.append((WIDE_CONT, (None, None, 0)))
    return cells


# ======================================================================
# Phase 14 Part 6 — rows_to_text
# ======================================================================
class TestRowsToText(unittest.TestCase):
    def test_plain_text(self):
        rows = [row("hello"), row("world")]
        self.assertEqual(rows_to_text(rows), ["hello", "world"])

    def test_empty_rows_preserved(self):
        rows = [row("a"), row(""), row("b")]
        self.assertEqual(rows_to_text(rows), ["a", "", "b"])

    def test_wide_chars_skip_continuations(self):
        rows = [wide_row("你好")]
        text = rows_to_text(rows)
        self.assertEqual(text, ["你好"])
        # The text is exactly the two wide chars — no extra cells.
        self.assertEqual(len(text[0]), 2)

    def test_mixed_wide_and_narrow(self):
        rows = [wide_row("hi 中")]
        self.assertEqual(rows_to_text(rows), ["hi 中"])


# ======================================================================
# Phase 14 Part 6 — compile_pattern
# ======================================================================
class TestCompilePattern(unittest.TestCase):
    def test_literal_pattern(self):
        p = compile_pattern("hello")
        self.assertTrue(p.search("hello world"))
        self.assertTrue(p.search("HELLO world"))     # case-insensitive by default

    def test_case_sensitive(self):
        p = compile_pattern("HELLO", case_sensitive=True)
        self.assertTrue(p.search("HELLO world"))
        self.assertFalse(p.search("hello world"))

    def test_regex_special_chars_escaped_in_literal_mode(self):
        p = compile_pattern("error.code")     # dot is literal, not regex-any
        self.assertTrue(p.search("error.code 42"))
        self.assertFalse(p.search("errorXcode 42"))

    def test_regex_mode(self):
        p = compile_pattern(r"code \d+", regex=True)
        self.assertTrue(p.search("error: code 42"))
        self.assertFalse(p.search("error: no match"))

    def test_empty_query_raises(self):
        with self.assertRaises(ValueError):
            compile_pattern("")


# ======================================================================
# Phase 14 Part 6 — find_matches
# ======================================================================
class TestFindMatches(unittest.TestCase):
    def test_single_match(self):
        rows = [row("hello world")]
        p = compile_pattern("world")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].row_index, 0)
        self.assertEqual(matches[0].col, 6)
        self.assertEqual(matches[0].length, 5)
        self.assertEqual(matches[0].text, "world")

    def test_multiple_matches_in_one_row(self):
        rows = [row("the cat sat on the mat")]
        p = compile_pattern("at")
        matches = find_matches(rows_to_text(rows), p)
        # "at" appears in "cat", "sat", "mat" = 3 matches
        self.assertEqual(len(matches), 3)
        # "the cat sat on the mat"
        #  0123456789...
        # "cat" starts at 4, "at" is at 5
        self.assertEqual(matches[0].col, 5)     # c-at
        # "sat" starts at 8, "at" is at 9
        self.assertEqual(matches[1].col, 9)     # s-at
        # "mat" starts at 19, "at" is at 20
        self.assertEqual(matches[2].col, 20)     # m-at

    def test_matches_across_rows(self):
        rows = [row("hello"), row("hello"), row("world")]
        p = compile_pattern("hello")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].row_index, 0)
        self.assertEqual(matches[1].row_index, 1)

    def test_no_matches(self):
        rows = [row("nothing here")]
        p = compile_pattern("xyz")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(matches, [])

    def test_match_does_not_span_rows(self):
        """Terminal text is line-oriented — a match can't wrap across rows."""
        rows = [row("hel"), row("lo")]
        p = compile_pattern("hello")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(matches, [])

    def test_max_matches_cap(self):
        rows = [row("a" * 100)]
        p = compile_pattern("a")
        matches = find_matches(rows_to_text(rows), p, max_matches=10)
        self.assertEqual(len(matches), 10)

    def test_regex_match(self):
        rows = [row("error: code 42"), row("error: code 99")]
        p = compile_pattern(r"code \d+", regex=True)
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].text, "code 42")
        self.assertEqual(matches[1].text, "code 99")

    def test_case_insensitive_default(self):
        rows = [row("Hello HELLO hello")]
        p = compile_pattern("hello")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(len(matches), 3)

    def test_wide_char_match(self):
        rows = [wide_row("你好 world")]
        p = compile_pattern("你好")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].col, 0)
        self.assertEqual(matches[0].text, "你好")

    def test_wide_char_col_is_char_offset_not_cell(self):
        """The match col is the character offset in the text, not the cell
        index — so a match after 你好 starts at col 3 (你好 + space), not 4."""
        rows = [wide_row("你好 world")]
        p = compile_pattern("world")
        matches = find_matches(rows_to_text(rows), p)
        self.assertEqual(matches[0].col, 3)     # 你好 = 2 chars, + space = 3


# ======================================================================
# Phase 14 Part 6 — search convenience wrapper
# ======================================================================
class TestSearchWrapper(unittest.TestCase):
    def test_empty_query_returns_empty(self):
        rows = [row("hello")]
        self.assertEqual(search(rows, ""), [])

    def test_invalid_regex_returns_empty(self):
        rows = [row("hello")]
        self.assertEqual(search(rows, "[", regex=True), [])

    def test_valid_regex_returns_matches(self):
        rows = [row("error 1"), row("error 2")]
        matches = search(rows, r"error \d", regex=True)
        self.assertEqual(len(matches), 2)

    def test_literal_search_works(self):
        rows = [row("hello world")]
        matches = search(rows, "hello")
        self.assertEqual(len(matches), 1)


# ======================================================================
# Phase 14 Part 6 — next_match navigation
# ======================================================================
class TestNextMatch(unittest.TestCase):
    def setUp(self):
        self.rows = [row("hello"), row("hello"), row("world"), row("hello")]
        self.matches = search(self.rows, "hello")
        self.assertEqual(len(self.matches), 3)

    def test_start_from_none_forward(self):
        i = next_match(self.matches, None, forward=True)
        self.assertEqual(i, 0)

    def test_start_from_none_backward(self):
        i = next_match(self.matches, None, forward=False)
        self.assertEqual(i, 2)     # last match

    def test_forward_increments(self):
        i = next_match(self.matches, 0, forward=True)
        self.assertEqual(i, 1)
        i = next_match(self.matches, 1, forward=True)
        self.assertEqual(i, 2)

    def test_forward_wraps_around(self):
        i = next_match(self.matches, 2, forward=True)
        self.assertEqual(i, 0)     # wraps to first

    def test_backward_decrements(self):
        i = next_match(self.matches, 2, forward=False)
        self.assertEqual(i, 1)
        i = next_match(self.matches, 1, forward=False)
        self.assertEqual(i, 0)

    def test_backward_wraps_around(self):
        i = next_match(self.matches, 0, forward=False)
        self.assertEqual(i, 2)     # wraps to last

    def test_empty_matches_returns_none(self):
        self.assertIsNone(next_match([], None, forward=True))
        self.assertIsNone(next_match([], 0, forward=True))


# ======================================================================
# Phase 14 Part 6 — match_at_row
# ======================================================================
class TestMatchAtRow(unittest.TestCase):
    def test_returns_matches_on_row(self):
        rows = [row("hello"), row("world"), row("hello")]
        matches = search(rows, "hello")
        # Row 0 has 1 match, row 2 has 1 match.
        self.assertEqual(len(match_at_row(matches, 0)), 1)
        self.assertEqual(len(match_at_row(matches, 1)), 0)
        self.assertEqual(len(match_at_row(matches, 2)), 1)

    def test_multiple_matches_on_same_row(self):
        rows = [row("the cat sat on the mat")]
        matches = search(rows, "at")
        # All 3 matches are on row 0.
        self.assertEqual(len(match_at_row(matches, 0)), 3)


# ======================================================================
# Phase 14 Part 6 — summary
# ======================================================================
class TestSummary(unittest.TestCase):
    def test_no_matches(self):
        self.assertEqual(summary([], None), "No matches.")

    def test_matches_none_current(self):
        self.assertEqual(summary([1, 2, 3], None), "3 matches")
        self.assertEqual(summary([1], None), "1 match")

    def test_matches_with_current(self):
        self.assertEqual(summary([1, 2, 3], 0), "1 of 3 matches")
        self.assertEqual(summary([1, 2, 3], 2), "3 of 3 matches")


# ======================================================================
# Phase 14 Part 6 — is_valid_regex
# ======================================================================
class TestIsValidRegex(unittest.TestCase):
    def test_valid_regex(self):
        ok, err = is_valid_regex(r"code \d+")
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_invalid_regex(self):
        ok, err = is_valid_regex("[")
        self.assertFalse(ok)
        self.assertIn("unterminated", err.lower())

    def test_empty_string_is_valid_regex(self):
        # An empty string is a valid (if useless) regex.
        ok, _ = is_valid_regex("")
        self.assertTrue(ok)


# ======================================================================
# Phase 14 Part 6 — Match dataclass
# ======================================================================
class TestMatchDataclass(unittest.TestCase):
    def test_end_col(self):
        m = Match(row_index=0, col=5, length=4, text="test")
        self.assertEqual(m.end_col, 9)

    def test_default_text_empty(self):
        m = Match(row_index=0, col=0, length=3)
        self.assertEqual(m.text, "")


# ======================================================================
# Phase 14 Part 6 — realistic scenarios
# ======================================================================
class TestRealisticScenarios(unittest.TestCase):
    def test_log_search(self):
        """A realistic log output: searching for 'error' in a stream of logs."""
        rows = [
            row("[2024-01-15 10:00:01] INFO: server started"),
            row("[2024-01-15 10:00:02] INFO: listening on :8080"),
            row("[2024-01-15 10:00:03] ERROR: connection refused"),
            row("[2024-01-15 10:00:04] WARN: retrying"),
            row("[2024-01-15 10:00:05] ERROR: connection refused again"),
        ]
        matches = search(rows, "error")
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].row_index, 2)
        self.assertEqual(matches[1].row_index, 4)

    def test_regex_log_search(self):
        """Searching for all timestamps with a regex."""
        rows = [
            row("[2024-01-15 10:00:01] INFO: started"),
            row("[2024-01-15 10:00:02] INFO: listening"),
        ]
        matches = search(rows, r"\d{4}-\d{2}-\d{2}", regex=True)
        self.assertEqual(len(matches), 2)
        for m in matches:
            self.assertEqual(m.length, 10)     # YYYY-MM-DD

    def test_cjk_log_search(self):
        """Searching for Chinese text in a log."""
        rows = [
            row("[2024-01-15] INFO: 服务器启动"),
            row("[2024-01-15] ERROR: 数据库连接失败"),
            row("[2024-01-15] WARN: 重试中"),
        ]
        # Search for 错误 (error in Chinese) — should not match
        matches = search(rows, "错误")
        self.assertEqual(len(matches), 0)
        # Search for 数据库 (database) — should match row 1
        matches = search(rows, "数据库")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].row_index, 1)

    def test_long_output_search(self):
        """A long output (1000 lines) — search should be fast and cap at 500."""
        rows = [row(f"line {i}: all good") for i in range(1000)]
        # Every line matches "line"
        matches = search(rows, "line")
        self.assertLessEqual(len(matches), 500)     # capped
        # A more specific search should match exactly 1.
        matches = search(rows, "line 42: ")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].row_index, 42)


if __name__ == "__main__":
    unittest.main()
