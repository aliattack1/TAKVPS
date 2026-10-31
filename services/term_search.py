"""Phase 14 Part 6 — Search in terminal scrollback.

Pure helper that finds matches in the terminal's text (scrollback + visible
screen) and returns their positions so the UI can highlight them and jump
to them with prev/next buttons.

The terminal emulator stores rows as lists of `(char, style)` cells (with
`WIDE_CONT` sentinels for the second cell of wide chars — see Phase 14 Part 3).
This module works on the plain-text projection of those rows, so it stays
decoupled from the emulator's internal representation.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence


@dataclass
class Match:
    """One search hit. `row_index` is the absolute row number across the
    combined scrollback+grid (0 = oldest row). `col` is the character offset
    within that row's text. `length` is the match length in characters."""
    row_index: int
    col: int
    length: int
    text: str = ""               # the matched substring (for the result list)

    @property
    def end_col(self) -> int:
        return self.col + self.length


def _row_text(row: list) -> str:
    """Join a row's cells into plain text, skipping `WIDE_CONT` sentinels.

    Mirrors `Screen.spans_of_row`'s skip logic, but returns a flat string
    instead of styled spans. Combining marks are preserved (they're stored
    as part of the base cell's string by `_put`).
    """
    from services.wcwidth import is_wide_continuation
    return "".join(ch for ch, _ in row if not is_wide_continuation(ch))


def rows_to_text(rows: Sequence[list]) -> list[str]:
    """Convert a sequence of rows (each a list of cells) into a list of
    plain-text strings. Empty rows become empty strings (not skipped), so
    the row index is preserved."""
    return [_row_text(r) for r in rows]


def compile_pattern(query: str, case_sensitive: bool = False,
                    regex: bool = False) -> re.Pattern:
    """Compile a search query into a regex Pattern.

    - `case_sensitive`: False (default) → case-insensitive match.
    - `regex`: False (default) → the query is treated as a literal string
      (special regex chars are escaped). True → the query is a regex.

    Raises `re.error` for invalid regex patterns.
    """
    if not query:
        raise ValueError("Search query is empty.")
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = query if regex else re.escape(query)
    return re.compile(pattern, flags)


def find_matches(text_rows: Sequence[str], pattern: re.Pattern,
                 max_matches: int = 500) -> list[Match]:
    """Find all matches of `pattern` across the rows.

    Returns a list of `Match` objects sorted by (row_index, col). Caps at
    `max_matches` so a pathological search (e.g. matching every character)
    doesn't blow up the UI.

    Each row is searched independently — matches don't span row boundaries
    (terminal text is line-oriented).
    """
    out: list[Match] = []
    for row_idx, text in enumerate(text_rows):
        for m in pattern.finditer(text):
            out.append(Match(row_index=row_idx, col=m.start(),
                             length=m.end() - m.start(), text=m.group()))
            if len(out) >= max_matches:
                return out
    return out


def search(rows: Sequence[list], query: str, case_sensitive: bool = False,
           regex: bool = False, max_matches: int = 500) -> list[Match]:
    """Convenience: compile + find in one call. Returns [] for empty query."""
    if not query:
        return []
    try:
        pattern = compile_pattern(query, case_sensitive, regex)
    except (re.error, ValueError):
        return []
    text_rows = rows_to_text(rows)
    return find_matches(text_rows, pattern, max_matches)


def next_match(matches: list[Match], current: Optional[int],
               forward: bool = True) -> Optional[int]:
    """Return the index into `matches` of the next match after `current`.

    `current` is an index into `matches` (not a row index), or None if no
    match is currently selected. Wraps around to the other end when it
    reaches the boundary.

    Returns None if `matches` is empty.
    """
    if not matches:
        return None
    if current is None:
        return 0 if forward else len(matches) - 1
    if forward:
        return (current + 1) % len(matches)
    return (current - 1) % len(matches)


def match_at_row(matches: list[Match], row_index: int) -> list[Match]:
    """All matches that fall on `row_index`. Used by the UI to highlight
    every match on the current row, not just the selected one."""
    return [m for m in matches if m.row_index == row_index]


def summary(matches: list[Match], current: Optional[int]) -> str:
    """One-line status for the search bar: '3 of 12 matches' or 'No matches.'."""
    if not matches:
        return "No matches."
    if current is None:
        return f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
    return f"{current + 1} of {len(matches)} matches"


def is_valid_regex(query: str) -> tuple[bool, str]:
    """Check if `query` is a valid regex. Returns (ok, error_message)."""
    try:
        re.compile(query)
        return True, ""
    except re.error as e:
        return False, str(e)
