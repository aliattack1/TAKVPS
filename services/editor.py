"""Editor logic: language detection, lightweight highlighter, find/replace, encodings. No UI."""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


# ------------------------------------------------------------------ encoding / EOL
def decode_bytes(data: bytes) -> tuple[str, str, str]:
    """-> (text with '\\n' endings, encoding, eol). Falls back to latin-1 (never fails)."""
    enc = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = data.decode(enc)
    except UnicodeDecodeError:
        enc, text = "latin-1", data.decode("latin-1")
    eol = "\r\n" if text.count("\r\n") > text.count("\n") / 2 and "\r\n" in text else "\n"
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, enc, eol


def encode_text(text: str, encoding: str, eol: str) -> bytes:
    if eol != "\n":
        text = text.replace("\n", eol)
    return text.encode(encoding, errors="replace")


# ------------------------------------------------------------------ languages / highlighting
@dataclass(frozen=True)
class Lang:
    name: str
    line_comments: tuple[str, ...] = ()
    block: Optional[tuple[str, str]] = None
    keywords: frozenset = frozenset()
    tags: bool = False             # highlight <tag names> (html/xml)


def _kw(s: str) -> frozenset:
    return frozenset(s.split())


_C_LIKE = _kw("if else for while do switch case break continue return function const let var class new "
              "import export from default try catch finally throw async await true false null undefined "
              "typeof in of this void static public private protected int string bool float double "
              "struct enum interface type fn func go defer package use mod pub impl let mut match self")
LANGS: dict[str, Lang] = {
    "python": Lang("python", ("#",), None, _kw(
        "def class if elif else for while return import from as try except finally raise with lambda "
        "pass break continue in is not and or None True False yield async await global nonlocal del assert")),
    "javascript": Lang("javascript", ("//",), ("/*", "*/"), _C_LIKE),
    "clike": Lang("clike", ("//",), ("/*", "*/"), _C_LIKE),
    "json": Lang("json", (), None, _kw("true false null")),
    "yaml": Lang("yaml", ("#",), None, _kw("true false null yes no on off")),
    "shell": Lang("shell", ("#",), None, _kw(
        "if then else elif fi for while do done case esac function in return exit export local echo "
        "cd set unset source sudo true false")),
    "conf": Lang("conf", ("#",), None, _kw(
        "server location listen root index proxy_pass include upstream worker_processes events http "
        "ssl_certificate ssl_certificate_key return rewrite try_files on off")),
    "ini": Lang("ini", (";", "#"), None, _kw("true false yes no on off")),
    "html": Lang("html", (), ("<!--", "-->"), frozenset(), tags=True),
    "css": Lang("css", (), ("/*", "*/"), _kw("important inherit initial none auto")),
    "sql": Lang("sql", ("--",), ("/*", "*/"), _kw(
        "select from where insert into values update set delete create table drop alter join left right "
        "inner outer on group by order limit and or not null as distinct index primary key")),
    "markdown": Lang("markdown", (), ("<!--", "-->")),
    "text": Lang("text"),
}
_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "javascript", ".jsx": "javascript",
        ".tsx": "javascript", ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".sh": "shell",
        ".bash": "shell", ".zsh": "shell", ".conf": "conf", ".cfg": "ini", ".ini": "ini", ".toml": "ini",
        ".service": "ini", ".timer": "ini", ".env": "ini", ".html": "html", ".htm": "html", ".xml": "html",
        ".css": "css", ".sql": "sql", ".md": "markdown", ".c": "clike", ".h": "clike", ".cpp": "clike",
        ".java": "clike", ".go": "clike", ".rs": "clike", ".php": "clike", ".log": "text", ".txt": "text"}
_NAMES = {"nginx.conf": "conf", "dockerfile": "shell", "makefile": "shell", ".bashrc": "shell",
          ".profile": "shell", "crontab": "shell", "sshd_config": "conf", "authorized_keys": "text"}


def language_for(filename: str) -> str:
    base = posixpath.basename(filename).lower()
    return _NAMES.get(base) or _EXT.get(posixpath.splitext(base)[1], "text")


@lru_cache(maxsize=32)
def _pattern(lang: str) -> re.Pattern:
    L = LANGS.get(lang, LANGS["text"])
    parts = []
    if L.block:
        parts.append(r"(?P<comment>%s.*?(?:%s|\Z))" % (re.escape(L.block[0]), re.escape(L.block[1])))
    for c in L.line_comments:
        parts.append(r"(?P<comment%d>%s[^\n]*)" % (len(parts), re.escape(c)))
    parts.append(r'(?P<string>"(?:\\.|[^"\\\n])*"?|\'(?:\\.|[^\'\\\n])*\'?|`[^`]*`?)')
    if L.tags:
        parts.append(r"(?P<tag></?[A-Za-z][\w:-]*|/?>)")
    parts.append(r"(?P<number>\b\d+(?:\.\d+)?\b)")
    parts.append(r"(?P<word>[A-Za-z_][\w-]*)")
    return re.compile("|".join(parts), re.S)


def tokenize(text: str, lang: str) -> list[tuple[str, str]]:
    """Cover `text` completely with (chunk, kind) pairs; kinds: plain/comment/string/number/keyword/tag."""
    L = LANGS.get(lang, LANGS["text"])
    if L is LANGS["text"] or len(text) > 400_000:
        return [(text, "plain")] if text else []
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _pattern(lang).finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], "plain"))
        kind = m.lastgroup or "plain"
        if kind.startswith("comment"):
            kind = "comment"
        elif kind == "word":
            kind = "keyword" if m.group() in L.keywords else "plain"
        out.append((m.group(), kind))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], "plain"))
    return out


def tokenize_lines(text: str, lang: str) -> list[list[tuple[str, str]]]:
    """Per-line span lists (multi-line tokens are split at newlines)."""
    lines: list[list[tuple[str, str]]] = [[]]
    for chunk, kind in tokenize(text, lang):
        pieces = chunk.split("\n")
        for i, piece in enumerate(pieces):
            if i:
                lines.append([])
            if piece:
                lines[-1].append((piece, kind))
    return lines


# ------------------------------------------------------------------ find / replace
def _compile(query: str, case: bool, regex: bool) -> re.Pattern:
    try:
        return re.compile(query if regex else re.escape(query), (0 if case else re.I) | re.M)
    except re.error as e:
        raise ValueError(f"Invalid pattern: {e}") from e


def find_all(text: str, query: str, case: bool = False, regex: bool = False,
             limit: int = 5000) -> list[tuple[int, int]]:
    if not query:
        return []
    out = []
    for m in _compile(query, case, regex).finditer(text):
        if m.end() > m.start():
            out.append((m.start(), m.end()))
            if len(out) >= limit:
                break
    return out


def replace_all(text: str, query: str, repl: str, case: bool = False,
                regex: bool = False) -> tuple[str, int]:
    if not query:
        return text, 0
    pat = _compile(query, case, regex)
    try:
        return pat.subn(repl if regex else (lambda _m: repl), text)
    except (re.error, IndexError) as e:
        raise ValueError(f"Invalid replacement: {e}") from e


def line_col(text: str, index: int) -> tuple[int, int]:
    index = max(0, min(index, len(text)))
    line = text.count("\n", 0, index) + 1
    return line, index - (text.rfind("\n", 0, index) + 1) + 1
