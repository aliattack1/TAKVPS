"""Atomic file writes (optionally owner-only permissions)."""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, text: str, private: bool = False) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
