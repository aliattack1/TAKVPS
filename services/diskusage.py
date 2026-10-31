"""Disk usage drill-down parsing and the cleanup assistant's catalogue. Pure."""
from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

_LOGS = r"\( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' -o -name '*.xz' -o -name '*.bz2' \)"


def du_command(path: str) -> str:
    if not path.startswith("/") or "\n" in path:
        raise ValueError("Enter an absolute path.")
    p = shlex.quote(path)
    return f"(du -xk --max-depth=1 {p} 2>/dev/null || du -xk -d 1 {p} 2>/dev/null) | sort -rn | head -n 60"


@dataclass
class DuEntry:
    path: str
    kb: int

    @property
    def name(self) -> str:
        return posixpath.basename(self.path.rstrip("/")) or "/"


def parse_du(text: str, base: str) -> tuple[int, list[DuEntry]]:
    base = posixpath.normpath(base)
    total, entries = 0, []
    for ln in text.splitlines():
        kb, _, path = ln.partition("\t")
        if not kb.strip().isdigit() or not path:
            continue
        if posixpath.normpath(path) == base:
            total = int(kb)
        else:
            entries.append(DuEntry(path, int(kb)))
    entries.sort(key=lambda e: -e.kb)
    return total or sum(e.kb for e in entries), entries


def parent(path: str) -> str:
    return posixpath.dirname(posixpath.normpath(path)) or "/"


_UNITS = {"": 1 / 1024, "b": 1 / 1024, "k": 1, "m": 1024, "g": 1024 ** 2, "t": 1024 ** 3}


def parse_size_text(s: str) -> Optional[int]:
    """'1.2GB', '350 MiB', '12K' → KB."""
    m = re.search(r"([\d.]+)\s*([KMGT]?)i?B?\b", s.replace(",", "."), re.I)
    if not m:
        return None
    try:
        return int(float(m.group(1)) * _UNITS[m.group(2).lower()])
    except (ValueError, KeyError):
        return None


@dataclass
class Cleanup:
    key: str
    title: str
    note: str
    measure: str                     # shell snippet printing a number (KB), a count, or text with a size
    clean: str
    unit: str = "kb"                 # kb | text | count
    caution: bool = False


def items_for(pm: str, has_docker: bool, has_systemd: bool) -> list[Cleanup]:
    out: list[Cleanup] = []
    if pm == "apt":
        out.append(Cleanup("pkgcache", "Package cache (apt)", "Downloaded .deb files; re-downloaded if needed.",
                           "du -sk /var/cache/apt/archives 2>/dev/null | cut -f1", "apt-get clean"))
        out.append(Cleanup("autoremove", "Unused packages & old kernels", "Removes packages nothing depends on (including old "
                           "kernels). Review with a dry run in the terminal if unsure.",
                           "apt-get -s autoremove 2>/dev/null | grep -c '^Remv'", "DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y",
                           "count", True))
    elif pm in ("dnf", "yum"):
        out.append(Cleanup("pkgcache", f"Package cache ({pm})", "Downloaded metadata and packages.",
                           "du -sck /var/cache/dnf /var/cache/yum 2>/dev/null | tail -n 1 | cut -f1", f"{pm} clean all"))
    elif pm == "pacman":
        out.append(Cleanup("pkgcache", "Package cache (pacman)", "Old package files (keeps installed versions).",
                           "du -sk /var/cache/pacman/pkg 2>/dev/null | cut -f1", "pacman -Sc --noconfirm"))
    elif pm == "apk":
        out.append(Cleanup("pkgcache", "Package cache (apk)", "Downloaded packages.", "du -sk /var/cache/apk 2>/dev/null | cut -f1",
                           "apk cache clean"))
    if has_systemd:
        out.append(Cleanup("journal", "System journal", "Total journal size now; the cleanup deletes entries older than 7 days.",
                           "journalctl --disk-usage 2>/dev/null", "journalctl --vacuum-time=7d", "text", True))
    out.append(Cleanup("oldlogs", "Rotated & compressed logs", "Old *.gz / *.1 / *.old files in /var/log.",
                       f"find /var/log -type f {_LOGS} -print0 2>/dev/null | du -ck --files0-from=- 2>/dev/null | tail -n 1 | cut -f1",
                       f"find /var/log -type f {_LOGS} -delete", "kb", True))
    if has_docker:
        df = "docker system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null | grep '^%s|'"
        out.append(Cleanup("dimages", "Unused Docker images", "Images not used by any container (removed with `image prune -a`).",
                           df % "Images", "docker image prune -af", "text", True))
        out.append(Cleanup("dvolumes", "Unused Docker volumes", "Volumes not used by any container. THIS DELETES DATA.",
                           df % "Local Volumes", "docker volume prune -f", "text", True))
        out.append(Cleanup("dbuild", "Docker build cache", "Cached build layers.", df % "Build Cache", "docker builder prune -af", "text"))
    return out


def measure_script(items: list[Cleanup]) -> str:
    parts = []
    for it in items:
        parts.append(f"echo '##{it.key}'\n({it.measure}) 2>/dev/null || echo 0")
    return "\n".join(parts) + "\n"


def parse_measure(text: str, items: list[Cleanup]) -> dict[str, Optional[int]]:
    """key -> KB (or a count for unit=='count'); None if it couldn't be measured."""
    raw: dict[str, str] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:]
            raw[cur] = ""
        elif cur:
            raw[cur] += ln + "\n"
    out: dict[str, Optional[int]] = {}
    for it in items:
        r = raw.get(it.key, "").strip()
        if not r:
            out[it.key] = None
        elif it.unit == "kb" or it.unit == "count":
            m = re.match(r"\d+", r)
            out[it.key] = int(m.group()) if m else None
        else:
            out[it.key] = parse_size_text(r.split("|", 1)[-1] if "|" in r else r)
    return out
