"""Users, authorized_keys parsing/validation, failed-login analysis. Pure."""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Optional

PASSWD_CMD = "getent passwd"
SUDO_GROUPS_CMD = "getent group sudo wheel admin 2>/dev/null"
LAST_CMD = "last -n 25 -w 2>/dev/null | head -n 30"
FAILED_CMDS = ["lastb -n 60 -w 2>/dev/null",
               "grep -h 'Failed password\\|Invalid user' /var/log/auth.log /var/log/secure 2>/dev/null | tail -n 80",
               "journalctl -u ssh -u sshd --no-pager -n 400 2>/dev/null | grep -i 'Failed password\\|Invalid user' | tail -n 80"]
KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ssh-dss", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
_NAME = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")


@dataclass
class User:
    name: str
    uid: int
    gid: int
    gecos: str
    home: str
    shell: str
    sudo: bool = False

    @property
    def human(self) -> bool:
        return (self.uid == 0 or self.uid >= 1000) and self.name != "nobody" and \
            not self.shell.endswith(("nologin", "false"))


def valid_username(n: str) -> bool:
    return bool(_NAME.match(n))


def parse_passwd(text: str, sudo_groups: str = "") -> list[User]:
    sudoers: set[str] = set()
    for ln in sudo_groups.splitlines():
        p = ln.split(":")
        if len(p) >= 4:
            sudoers.update(x for x in p[3].split(",") if x)
    out = []
    for ln in text.splitlines():
        p = ln.split(":")
        if len(p) >= 7 and p[2].isdigit() and p[3].isdigit():
            out.append(User(p[0], int(p[2]), int(p[3]), p[4].split(",")[0], p[5], p[6].strip(),
                            p[0] in sudoers or int(p[2]) == 0))
    return sorted(out, key=lambda u: (u.uid != 0, not u.human, u.name))


# ------------------------------------------------------------------ authorized_keys
@dataclass
class AuthLine:
    raw: str
    options: str = ""
    type: str = ""
    blob: str = ""
    comment: str = ""
    fingerprint: str = ""

    @property
    def is_key(self) -> bool:
        return bool(self.blob)


def fingerprint(blob_b64: str) -> str:
    raw = base64.b64decode(blob_b64 + "=" * (-len(blob_b64) % 4))
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def _embedded_type(blob_b64: str) -> Optional[str]:
    try:
        raw = base64.b64decode(blob_b64 + "=" * (-len(blob_b64) % 4), validate=True)
        n = int.from_bytes(raw[:4], "big")
        return raw[4:4 + n].decode()
    except Exception:  # noqa: BLE001
        return None


_KEY = re.compile(r"(?:^|\s)(" + "|".join(re.escape(t) for t in KEY_TYPES) + r")\s+([A-Za-z0-9+/=]+)(?:\s+(.*))?$")


def parse_line(raw: str) -> AuthLine:
    s = raw.strip()
    if not s or s.startswith("#"):
        return AuthLine(raw)
    m = _KEY.search(s)
    if not m or _embedded_type(m.group(2)) != m.group(1):
        return AuthLine(raw)
    return AuthLine(raw, s[:m.start()].strip(), m.group(1), m.group(2), (m.group(3) or "").strip(),
                    fingerprint(m.group(2)))


def parse_authorized_keys(text: str) -> list[AuthLine]:
    return [parse_line(l) for l in text.splitlines()]


def serialize(lines: list[AuthLine]) -> str:
    t = "\n".join(l.raw for l in lines)
    return t + "\n" if t and not t.endswith("\n") else t


def validate_new_key(text: str, existing: list[AuthLine]) -> tuple[Optional[AuthLine], str]:
    s = " ".join(text.strip().split())
    if "\n" in text.strip():
        return None, "Paste one key at a time."
    k = parse_line(s)
    if not k.is_key:
        return None, "That doesn't look like a valid public key (expected e.g. “ssh-ed25519 AAAA… comment”)."
    if "PRIVATE KEY" in text:
        return None, "That's a private key. Never paste private keys here — use the public key (.pub)."
    if any(e.blob == k.blob for e in existing):
        return None, "That key is already authorized."
    return k, ""


def key_count(lines: list[AuthLine]) -> int:
    return sum(1 for l in lines if l.is_key)


# ------------------------------------------------------------------ failed logins
_IP = re.compile(r"\bfrom\s+((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{3,45})\b|\s((?:\d{1,3}\.){3}\d{1,3})\s")


def top_ips(text: str, n: int = 10) -> list[tuple[str, int]]:
    from collections import Counter
    c: Counter = Counter()
    for ln in text.splitlines():
        m = _IP.search(ln)
        if m:
            c[m.group(1) or m.group(2)] += 1
    return c.most_common(n)
