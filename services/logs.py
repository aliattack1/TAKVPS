"""Log sources, commands, severity and filtering. Pure."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

LIST_CMD = "ls -1p /var/log 2>/dev/null"
_ROTATED = re.compile(r"(\.\d+|\.gz|\.xz|\.bz2|\.zst|\.old)$")
_BINARY = {"wtmp", "btmp", "lastlog", "faillog", "tallylog"}
_PATH = re.compile(r"^/var/log/[\w.@-]+(/[\w.@-]+)*$")


@dataclass
class Source:
    id: str
    label: str
    cmd: str            # contains {n} for the number of lines
    follow: str         # command that streams new lines


PRESETS = [
    Source("journal", "System journal", "journalctl -n {n} --no-pager", "journalctl -n 50 -f --no-pager"),
    Source("journal_err", "Errors only (journal)", "journalctl -p err -n {n} --no-pager", "journalctl -p err -n 20 -f --no-pager"),
    Source("kernel", "Kernel messages", "dmesg -T 2>/dev/null | tail -n {n}", "journalctl -k -n 20 -f --no-pager"),
    Source("ssh", "SSH logins (journal)", "journalctl -u ssh -u sshd -n {n} --no-pager", "journalctl -u ssh -u sshd -n 20 -f --no-pager"),
]


def parse_log_files(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        n = ln.strip()
        if n and not n.endswith("/") and n not in _BINARY and not _ROTATED.search(n) and _PATH.match("/var/log/" + n):
            out.append(n)
    return sorted(out, key=str.lower)


def file_source(name: str) -> Source:
    path = "/var/log/" + name
    if not _PATH.match(path) or ".." in path:
        raise ValueError("Invalid log file name.")
    return Source("file:" + name, name, f"tail -n {{n}} {path}", f"tail -n 50 -F {path}")


def sources(files: list[str]) -> list[Source]:
    return PRESETS + [file_source(f) for f in files]


def command(src: Source, n: int = 300) -> str:
    return src.cmd.format(n=max(10, min(int(n), 5000)))


_ERR = re.compile(r"\b(error|err|fail(ed|ure)?|fatal|crit(ical)?|panic|denied|refused|segfault|oom|killed)\b", re.I)
_WARN = re.compile(r"\b(warn(ing)?|timeout|timed out|retry|deprecated|unable|invalid)\b", re.I)


def severity(line: str) -> str:
    if _ERR.search(line):
        return "error"
    if _WARN.search(line):
        return "warn"
    return ""


def filter_lines(lines: list[str], query: str = "", case: bool = False, regex: bool = False) -> list[str]:
    if not query:
        return lines
    if regex:
        try:
            pat = re.compile(query, 0 if case else re.I)
        except re.error as e:
            raise ValueError(f"Invalid pattern: {e}") from e
        return [l for l in lines if pat.search(l)]
    q = query if case else query.lower()
    return [l for l in lines if q in (l if case else l.lower())]
