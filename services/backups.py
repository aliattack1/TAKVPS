"""Backup commands: tar archives on the server, optional cron line. Pure."""
from __future__ import annotations

import posixpath
import re
import shlex
import time
from typing import Optional


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")[:40] or "backup"


def backup_filename(name: str, ts: float) -> str:
    return f"{slug(name)}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(ts))}.tar.gz"


def validate_paths(src: str, dest: str) -> Optional[str]:
    for label, p in (("Folder to back up", src), ("Backup folder", dest)):
        if not p.startswith("/") or "\n" in p or "\x00" in p:
            return f"{label} must be an absolute path (starting with /)."
    if posixpath.normpath(src) == "/":
        return "Backing up the whole filesystem (/) isn't supported."
    s, d = posixpath.normpath(src), posixpath.normpath(dest)
    if d == s or d.startswith(s + "/"):
        return "The backup folder can't be inside the folder you're backing up."
    return None


def _excludes(excludes: list[str]) -> str:
    return " ".join(f"--exclude={shlex.quote(e.strip())}" for e in excludes if e.strip() and "\n" not in e)


def archive_command(src: str, dest_dir: str, name: str, ts: float, excludes: list[str]) -> tuple[str, str]:
    """-> (shell command, remote path of the archive)."""
    if (err := validate_paths(src, dest_dir)):
        raise ValueError(err)
    parent, base = posixpath.split(posixpath.normpath(src))
    path = posixpath.join(posixpath.normpath(dest_dir), backup_filename(name, ts))
    q = shlex.quote
    cmd = (f"mkdir -p {q(dest_dir)} && tar -czf {q(path)} {_excludes(excludes)} -C {q(parent or '/')} {q(base)} "
           f"&& ls -lh {q(path)}")
    return re.sub(r"\s{2,}", " ", cmd), path


def cron_command(src: str, dest_dir: str, name: str, excludes: list[str], keep_days: int = 0) -> str:
    """One-line cron command; `%` is escaped for crontab. Optionally prunes archives older than keep_days."""
    if (err := validate_paths(src, dest_dir)):
        raise ValueError(err)
    parent, base = posixpath.split(posixpath.normpath(src))
    q = shlex.quote
    d = posixpath.normpath(dest_dir)
    cmd = (f"mkdir -p {q(d)} && tar -czf {q(d)}/{slug(name)}-$(date +%Y%m%d-%H%M%S).tar.gz {_excludes(excludes)} "
           f"-C {q(parent or '/')} {q(base)}")
    if keep_days > 0:
        cmd += f" && find {q(d)} -maxdepth 1 -name {q(slug(name) + '-*.tar.gz')} -mtime +{int(keep_days)} -delete"
    return re.sub(r"\s{2,}", " ", cmd).replace("%", "\\%")


def parse_df_avail(text: str) -> int:
    """Available KB from the last line of `df -Pk`."""
    try:
        return int(text.strip().splitlines()[-1].split()[3])
    except (IndexError, ValueError):
        return 0


def is_backup_file(filename: str) -> bool:
    return filename.endswith((".tar.gz", ".tgz"))
