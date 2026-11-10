"""File-manager logic: paths, sorting, filtering, permissions, safety checks. No UI code."""
from __future__ import annotations

import posixpath
import re
from typing import Optional

from core.models import RemoteEntry

EDIT_WARN_BYTES = 1_000_000
EDIT_MAX_BYTES = 5_000_000

_PROTECTED = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/opt",
              "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var", "/mnt", "/media",
              "/tmp", "/snap"}


def normalize(path: str) -> str:
    p = posixpath.normpath("/" + path.lstrip("/")) if not path.startswith("/") else posixpath.normpath(path)
    return "/" if p.startswith("//") and set(p) == {"/"} else p


def join(base: str, name: str) -> str:
    return posixpath.join(base, name)


def parent(path: str) -> str:
    return posixpath.dirname(normalize(path).rstrip("/")) or "/"


def basename(path: str) -> str:
    return posixpath.basename(path.rstrip("/"))


def breadcrumbs(path: str) -> list[tuple[str, str]]:
    """[(label, absolute_path)], root first."""
    out = [("/", "/")]
    cur = ""
    for part in [p for p in normalize(path).split("/") if p]:
        cur += "/" + part
        out.append((part, cur))
    return out


def valid_name(name: str) -> Optional[str]:
    """Return an error message, or None if the name is acceptable."""
    if not name.strip():
        return "Name cannot be empty."
    if name in (".", ".."):
        return "That name is not allowed."
    if "/" in name or "\x00" in name:
        return "Name cannot contain '/'."
    if len(name.encode()) > 255:
        return "Name is too long."
    return None


def is_subpath(child: str, ancestor: str) -> bool:
    c, a = normalize(child), normalize(ancestor)
    return c == a or c.startswith(a.rstrip("/") + "/")


def is_protected(path: str, home: str) -> bool:
    """Never allow deleting '/', the home folder itself, or top-level system folders."""
    p = normalize(path)
    return p in _PROTECTED or p == normalize(home)


def perms_string(mode: int, is_dir: bool = False, is_link: bool = False) -> str:
    t = "d" if is_dir else "l" if is_link else "-"
    out = []
    for i, ch in enumerate("rwxrwxrwx"):
        out.append(ch if mode & (1 << (8 - i)) else "-")
    if mode & 0o4000:
        out[2] = "s" if out[2] == "x" else "S"
    if mode & 0o2000:
        out[5] = "s" if out[5] == "x" else "S"
    if mode & 0o1000:
        out[8] = "t" if out[8] == "x" else "T"
    return t + "".join(out)


def perms_octal(mode: int) -> str:
    return f"{mode & 0o7777:04o}"


def _ext(name: str) -> str:
    return posixpath.splitext(name)[1].lower()


def sort_entries(entries: list[RemoteEntry], key: str = "name", reverse: bool = False) -> list[RemoteEntry]:
    """Folders always first; `reverse` flips order inside each group."""
    keyf = {"name": lambda e: e.name.casefold(),
            "size": lambda e: (e.size, e.name.casefold()),
            "date": lambda e: (e.mtime, e.name.casefold()),
            "type": lambda e: (_ext(e.name), e.name.casefold())}.get(key, lambda e: e.name.casefold())
    dirs = sorted((e for e in entries if e.is_dir), key=keyf if key != "size" else (lambda e: e.name.casefold()),
                  reverse=reverse)
    files = sorted((e for e in entries if not e.is_dir), key=keyf, reverse=reverse)
    return dirs + files


def filter_entries(entries: list[RemoteEntry], query: str = "", show_hidden: bool = False) -> list[RemoteEntry]:
    q = query.strip().casefold()
    return [e for e in entries
            if (show_hidden or not e.name.startswith("."))
            and (not q or q in e.name.casefold())]


def parse_du_kb(out: str) -> Optional[int]:
    """Bytes from `du -sk` output ("<kb>\t<path>"), or None."""
    m = re.match(r"\s*(\d+)\s", out or "")
    return int(m.group(1)) * 1024 if m else None


def check_editable(size: int, head: bytes) -> tuple[str, str]:
    """("ok" | "warn" | "refuse", reason). Binary sniffing on the first bytes."""
    if b"\x00" in head:
        return "refuse", "This looks like a binary file, so it can't be opened in the editor."
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        if not e.reason.startswith("unexpected end"):   # a multibyte char cut by the sample boundary is fine
            return "warn", "This file isn't valid UTF-8; characters may be replaced if you save it."
    if size > EDIT_MAX_BYTES:
        return "refuse", f"This file is too large to edit on a phone ({size // 1_000_000} MB). Download it instead."
    if size > EDIT_WARN_BYTES:
        return "warn", "This is a large file and may be slow to edit."
    return "ok", ""
