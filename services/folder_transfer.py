"""Recursive folder transfers (Phase 13.3 — Two-Pane Transfer).

The existing transfer queue in `services/transfers.py` only handles single files:
each entry has one `remote` and one `local` path. Folder transfers walk the tree
on one side and create matching directories on the other, then queue each file
as a normal transfer so the existing progress/cancel/history machinery applies.

Pure logic: given a tree description (list of relative paths + sizes), produce
the per-file transfer entries. The actual walking is done over SFTP (for the
remote side) or os.walk (for the local side) by the caller — this module is
testable without a network.
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional


@dataclass
class TreeNode:
    rel: str               # path relative to the root, posix-style ("sub/file.txt")
    is_dir: bool
    size: int = 0


@dataclass
class FolderTransferPlan:
    """One folder transfer broken into a list of file transfers + dirs to create."""
    kind: str               # "down" (remote → local) | "up" (local → remote)
    name: str               # display name (the root folder name)
    src_root: str           # absolute path on the source side
    dst_root: str           # absolute path on the destination side
    dirs: list[str] = field(default_factory=list)         # relative posix paths to mkdir
    files: list[TreeNode] = field(default_factory=list)   # one per file
    total_bytes: int = 0

    @property
    def file_count(self) -> int:
        return len(self.files)


def _posix(rel: str) -> str:
    """Normalize a relative path to posix (so `/` even on Windows)."""
    return rel.replace(os.sep, "/").replace("\\", "/").strip("/")


def build_plan_down(remote_entries: list[TreeNode], remote_root: str,
                    local_root: str, name: str) -> FolderTransferPlan:
    """Build a download plan from a remote listing.

    `remote_entries` is the full tree (recursive) under `remote_root`. The plan
    lists every directory to create on the local side, then every file to fetch.
    """
    dirs, files = [], []
    total = 0
    for e in remote_entries:
        rel = _posix(e.rel)
        if e.is_dir:
            dirs.append(rel)
        else:
            files.append(TreeNode(rel=rel, is_dir=False, size=e.size))
            total += e.size
    return FolderTransferPlan("down", name, remote_root, local_root, dirs, files, total)


def build_plan_up(local_root: str, remote_root: str, name: str,
                  walk: Optional[callable] = None) -> FolderTransferPlan:
    """Build an upload plan from a local directory.

    `walk` is `os.walk` by default; pass a fake for tests. Returns a plan with
    the local files (absolute paths) and relative posix paths for the remote side.
    """
    walk = walk or os.walk
    dirs, files = [], []
    total = 0
    root = Path(local_root)
    for dirpath, dirnames, filenames in walk(local_root):
        rel_dir = _posix(str(Path(dirpath).relative_to(root))) if dirpath != local_root else ""
        if rel_dir:
            dirs.append(rel_dir)
        for fn in filenames:
            rel = _posix(f"{rel_dir}/{fn}" if rel_dir else fn)
            abs_p = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(abs_p)
            except OSError:
                size = 0
            files.append(TreeNode(rel=rel, is_dir=False, size=size))
            total += size
    return FolderTransferPlan("up", name, local_root, remote_root, dirs, files, total)


def plan_entries(plan: FolderTransferPlan) -> Iterator[tuple[str, str, int]]:
    """Yield (src_abs, dst_abs, size) for each file in the plan.

    For downloads: src is remote posix path, dst is local OS path.
    For uploads: src is local OS path, dst is remote posix path.
    """
    for f in plan.files:
        rel = f.rel
        if plan.kind == "down":
            src = posixpath.join(plan.src_root, rel)
            dst = str(Path(plan.dst_root) / PurePosixPath(rel))
            # On Windows, Path joins with `\`; the SFTP put path needs posix.
            # Caller passes the OS path to local file I/O, so this is fine.
        else:
            src = os.path.join(plan.src_root, *rel.split("/"))
            dst = posixpath.join(plan.dst_root, rel)
        yield src, dst, f.size


def make_dirs_command(remote_paths: list[str]) -> str:
    """A single `mkdir -p a b c` for a batch of remote dirs. Empty list → 'true'."""
    paths = [p for p in remote_paths if p]
    if not paths:
        return "true"
    import shlex
    return "mkdir -p -- " + " ".join(shlex.quote(p) for p in paths)


def make_local_dirs(local_root: str, rel_paths: list[str]) -> None:
    """Create the local dirs for a download. Idempotent. No-op for empty list."""
    root = Path(local_root)
    root.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        if not rel:
            continue
        (root / PurePosixPath(rel)).mkdir(parents=True, exist_ok=True)


def estimated_file_count(plan: FolderTransferPlan) -> int:
    """Cheap progress denominator: file count + dir count (mkdirs add a small fixed cost)."""
    return plan.file_count + len(plan.dirs)


def truncate_for_display(plan: FolderTransferPlan, max_files: int = 200) -> str:
    """One-line summary for the transfer dialog ('42 files, 1.2 MB in 8 folders')."""
    n = plan.file_count
    mb = plan.total_bytes / (1024 * 1024)
    dirs = len(plan.dirs)
    if mb >= 1:
        size = f"{mb:.1f} MB"
    elif plan.total_bytes >= 1024:
        size = f"{plan.total_bytes / 1024:.0f} KB"
    else:
        size = f"{plan.total_bytes} B"
    return f"{n} files · {size} · {dirs} folder{'s' if dirs != 1 else ''}"
