"""Plain data models shared across layers (no third-party imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Credentials:
    """Everything needed to open one SSH hop. Secrets are excluded from repr."""
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = field(default=None, repr=False)
    private_key: Optional[str] = field(default=None, repr=False)
    passphrase: Optional[str] = field(default=None, repr=False)
    jump: Optional["Credentials"] = None     # ProxyJump-style hop (may chain)


@dataclass
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: int
    perms: int                 # permission bits only (0o7777)
    owner: str = ""
    group: str = ""
    is_link: bool = False


def parse_longname(longname) -> tuple[str, str]:
    """Owner/group names from an SFTP `ls -l`-style longname; ("", "") if unavailable."""
    if isinstance(longname, bytes):
        longname = longname.decode(errors="replace")
    if not isinstance(longname, str):
        return "", ""
    parts = longname.split(None, 4)
    return (parts[2], parts[3]) if len(parts) >= 5 else ("", "")
