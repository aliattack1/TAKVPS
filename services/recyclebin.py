"""Recycle Bin for deleted files .

Instead of permanently deleting files, move them to a per-server recycle bin
directory on the server itself (so a recycled file survives even if the phone
is wiped). The bin lives at `~/.takvps-recycle/` and is created on demand.

Layout:
    ~/.takvps-recycle/
        1730254881-etc-nginx.conf          # timestamp + flattened path
        1730254912-home-me-deploy.sh
        1730254915-var-log-app.log

The flattened path is "timestamp + original-path-with-slashes-replaced-by-dashes",
so a restore can move it back without name collisions. A sidecar JSON file
(`.meta/<entry>.json`) records the original absolute path so we can restore
even after the user renames files in the bin.

Pure logic only. The SSH object needs `run(cmd, timeout=)`, `move(src, dst)`,
`mkdir(path)`, `remove(path, recursive=)` and `exists(path)` — all already on
`core.ssh_client.SSHClient`.
"""
from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

BIN_DIR = ".takvps-recycle"
META_DIR = ".meta"
_FLATTEN_RE = re.compile(r"[^A-Za-z0-9._-]")


def bin_path(home: str) -> str:
    """Absolute path to the recycle bin on the server (`home/.takvps-recycle`)."""
    home = (home or "").rstrip("/") or "/"
    return f"{home}/{BIN_DIR}"


def meta_path(home: str, entry_name: str) -> str:
    return f"{bin_path(home)}/{META_DIR}/{entry_name}.json"


def _flatten(remote_path: str, ts: int) -> str:
    """`/etc/nginx/nginx.conf` + 1730254881 → `1730254881-etc-nginx-nginx.conf`."""
    p = remote_path.lstrip("/")
    flat = _FLATTEN_RE.sub("-", p).strip("-")
    return f"{ts}-{flat}" if flat else str(ts)


def safe_entry_name(orig_path: str, ts: Optional[int] = None) -> str:
    """Pick a unique entry name for a file about to be recycled."""
    ts = ts if ts is not None else int(time.time())
    return _flatten(orig_path, ts)


@dataclass
class BinEntry:
    name: str               # filename inside the bin
    orig_path: str          # absolute original path
    size: int
    mtime: float            # when recycled
    is_dir: bool


def entry_meta(name: str, orig_path: str, is_dir: bool = False, size: int = 0,
               mtime: Optional[float] = None) -> dict:
    return {
        "name": name,
        "orig_path": orig_path,
        "is_dir": is_dir,
        "size": size,
        "mtime": mtime if mtime is not None else time.time(),
        "v": 1,
    }


def serialize_meta(meta: dict) -> str:
    return json.dumps(meta, indent=2)


def parse_meta(text: str) -> Optional[dict]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) and "orig_path" in obj else None
    except (ValueError, TypeError):
        return None


# ---- shell command builders ------------------------------------------------

def ensure_bin_command(home: str) -> str:
    """Create the bin dir + meta subdir (idempotent)."""
    bin_ = shlex.quote(bin_path(home))
    return f"mkdir -p {bin_} {bin_}/.meta && chmod 700 {bin_}"


def recycle_command(home: str, remote_path: str, entry_name: str) -> tuple[str, str]:
    """Return (move_cmd, write_meta_cmd). Caller runs them in sequence.

    Move is `mv` (so it works across filesystems and preserves perms). The meta
    is written with a heredoc so the JSON content can't be misinterpreted.
    """
    bin_ = bin_path(home)
    target = f"{bin_}/{entry_name}"
    meta = entry_meta(entry_name, remote_path)
    move_cmd = f"mv -- {shlex.quote(remote_path)} {shlex.quote(target)}"
    write_meta_cmd = (
        f"cat > {shlex.quote(meta_path(home, entry_name))} <<'TAKVPS_META_EOF'\n"
        f"{serialize_meta(meta)}\n"
        f"TAKVPS_META_EOF"
    )
    return move_cmd, write_meta_cmd


def list_command(home: str) -> str:
    """One-shot list: stat each entry + cat its meta. Output is `name|size|mtime|is_dir|orig_path`."""
    bin_ = bin_path(home)
    # ls -1A (no . or ..), then for each entry run stat and read its meta.
    # We use `find` to be portable; `-mindepth 1 -maxdepth 1` skips the meta subdir.
    return (
        f"cd {shlex.quote(bin_)} && "
        f"find . -mindepth 1 -maxdepth 1 -type f -o -mindepth 1 -maxdepth 1 -type d "
        f"| while read -r e; do "
        f"name=$(basename \"$e\"); "
        f"[ \"$name\" = \".meta\" ] && continue; "
        f"meta=$(cat .meta/\"$name\".json 2>/dev/null || echo '{{}}'); "
        f"size=$(stat -c '%s' \"$e\" 2>/dev/null || echo 0); "
        f"mtime=$(stat -c '%Y' \"$e\" 2>/dev/null || echo 0); "
        f"is_dir=$([ -d \"$e\" ] && echo 1 || echo 0); "
        f"orig=$(echo \"$meta\" | grep -o '\"orig_path\": *\"[^\"]*\"' | sed 's/.*: *\"//;s/\"$//'); "
        f"echo \"$name|$size|$mtime|$is_dir|$orig\"; "
        f"done 2>/dev/null"
    )


def parse_list(out: str) -> list[BinEntry]:
    entries: list[BinEntry] = []
    for line in (out or "").splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        name, size, mtime, is_dir, orig = parts
        try:
            entries.append(BinEntry(
                name=name, orig_path=orig or "",
                size=int(size or 0), mtime=float(mtime or 0),
                is_dir=is_dir == "1"))
        except ValueError:
            continue
    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries


def restore_command(home: str, entry_name: str, orig_path: str,
                    overwrite: bool = False) -> tuple[str, str]:
    """Move an entry back to its original path. Returns (move_cmd, delete_meta_cmd).

    The caller MUST create any missing parent directories first. If the original
    path now exists, the caller decides what to do; pass overwrite=True to clobber.
    """
    bin_ = bin_path(home)
    src = f"{bin_}/{entry_name}"
    dst = orig_path
    flag = "" if overwrite else ""
    move_cmd = f"mv {flag} -- {shlex.quote(src)} {shlex.quote(dst)}"
    delete_meta_cmd = f"rm -f -- {shlex.quote(meta_path(home, entry_name))}"
    return move_cmd, delete_meta_cmd


def ensure_parent_command(orig_path: str) -> str:
    """Create the parent directory of `orig_path` (so restore can mv into it)."""
    parent = str(PurePosixPath(orig_path).parent) if orig_path else "/"
    return f"mkdir -p -- {shlex.quote(parent)}"


def delete_entry_command(home: str, entry_name: str, is_dir: bool = False) -> str:
    """Permanently delete one entry from the bin."""
    bin_ = bin_path(home)
    target = f"{bin_}/{entry_name}"
    flag = "-rf" if is_dir else "-f"
    meta = meta_path(home, entry_name)
    return f"rm {flag} -- {shlex.quote(target)} && rm -f -- {shlex.quote(meta)}"


def empty_bin_command(home: str) -> str:
    """Wipe the bin (entries + meta), keep the dir itself."""
    bin_ = bin_path(home)
    # Find everything in the bin, skip the .meta subdir's parent... actually
    # .meta itself should be wiped too, but the bin dir + meta subdir recreated.
    return (
        f"cd {shlex.quote(bin_)} && "
        f"find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} + 2>/dev/null; "
        f"mkdir -p .meta && chmod 700 {shlex.quote(bin_)}"
    )


def purge_older_than_command(home: str, days: int) -> str:
    """Auto-cleanup: delete entries older than N days (cron-friendly)."""
    if days <= 0:
        raise ValueError("days must be positive")
    bin_ = bin_path(home)
    return (
        f"find {shlex.quote(bin_)} -mindepth 1 -maxdepth 1 -mtime +{int(days)} "
        f"-not -path '*/.meta' -exec rm -rf -- {{}} + 2>/dev/null; true"
    )


def is_protected(path: str, home: str = "") -> tuple[bool, str]:
    """Refuse to recycle paths that would brick the server (`/`, `/etc`, ...).

    Returns (blocked, reason). Called before the move so the UI can show why.
    """
    p = (path or "").rstrip("/")
    if not p or p == "/":
        return True, "Refusing to recycle the root filesystem."
    # Top-level system folders (the same guard the File Manager uses).
    top = {"bin", "boot", "dev", "etc", "lib", "lib64", "proc", "root", "run",
           "sbin", "srv", "sys", "usr", "var"}
    parts = p.lstrip("/").split("/", 1)
    if parts[0] in top and len(parts) == 1:
        return True, f"Refusing to recycle a top-level system folder: /{parts[0]}"
    # The bin itself.
    if home and p == bin_path(home).rstrip("/"):
        return True, "Refusing to recycle the recycle bin itself."
    return False, ""
