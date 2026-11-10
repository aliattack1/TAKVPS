"""Saved connections. Secrets are only ever stored as vault-encrypted tokens."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Optional

from core.models import Credentials
from .fsutil import atomic_write
from .vault import Vault, VaultError, decrypt_with_passphrase, encrypt_with_passphrase

AUTH_TYPES = ("password", "key")


class ConnectionConfigError(ValueError):
    """Invalid profile / jump chain / import file. Message is user-facing."""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class ConnectionProfile:
    name: str
    host: str
    username: str
    port: int = 22
    auth: str = "password"
    tags: list[str] = field(default_factory=list)
    color: str = "#3B82F6"
    jump_id: str = ""
    id: str = field(default_factory=new_id)
    last_connected: float = 0.0
    password_enc: str = ""
    key_enc: str = ""
    passphrase_enc: str = ""
    # per-server custom instructions for the AI assistant.
    # Free-form text the user writes (e.g. "This is production — don't restart
    # nginx during 09:00–17:00 UTC"). Appended to the system prompt when this
    # server is the active connection.
    ai_context: str = ""

    @property
    def target(self) -> str:
        return f"{self.username}@{self.host}:{self.port}"

    @property
    def has_saved_secret(self) -> bool:
        return bool(self.password_enc if self.auth == "password" else self.key_enc)


@dataclass
class Secrets:
    """Plaintext secrets, transient. None = leave unchanged, "" = clear."""
    password: Optional[str] = None
    private_key: Optional[str] = None
    passphrase: Optional[str] = None


_PROFILE_FIELDS = {f.name for f in fields(ConnectionProfile)}


class ConnectionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, ConnectionProfile] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        for d in raw.get("profiles", []):
            try:
                p = ConnectionProfile(**{k: v for k, v in d.items() if k in _PROFILE_FIELDS})
                self._items[p.id] = p
            except TypeError:
                continue

    def _flush(self) -> None:
        data = {"v": 1, "profiles": [asdict(p) for p in self._items.values()]}
        atomic_write(self.path, json.dumps(data, indent=2), private=True)

    # ---- queries ----
    def list(self) -> list[ConnectionProfile]:
        return sorted(self._items.values(), key=lambda p: p.name.lower())

    def get(self, pid: str) -> Optional[ConnectionProfile]:
        return self._items.get(pid)

    # ---- validation ----
    def validate(self, p: ConnectionProfile) -> None:
        if not p.name.strip():
            raise ConnectionConfigError("Please enter a name.")
        if not p.host.strip():
            raise ConnectionConfigError("Please enter a host.")
        if not p.username.strip():
            raise ConnectionConfigError("Please enter a username.")
        if not (1 <= p.port <= 65535):
            raise ConnectionConfigError("Port must be between 1 and 65535.")
        if p.auth not in AUTH_TYPES:
            raise ConnectionConfigError("Unknown authentication type.")
        seen, j = {p.id}, p.jump_id
        while j:
            if j in seen:
                raise ConnectionConfigError("Jump host chain contains a loop.")
            nxt = self._items.get(j)
            if nxt is None:
                raise ConnectionConfigError("The selected jump host no longer exists.")
            seen.add(j)
            j = nxt.jump_id

    # ---- mutations ----
    def save(self, p: ConnectionProfile, secrets: Optional[Secrets] = None,
             vault: Optional[Vault] = None) -> None:
        self.validate(p)
        if secrets is not None:
            if vault is None:
                raise VaultError("A vault is required to store secrets.")
            if secrets.password is not None:
                p.password_enc = vault.encrypt(secrets.password) if secrets.password else ""
            if secrets.private_key is not None:
                p.key_enc = vault.encrypt(secrets.private_key) if secrets.private_key else ""
            if secrets.passphrase is not None:
                p.passphrase_enc = vault.encrypt(secrets.passphrase) if secrets.passphrase else ""
        self._items[p.id] = p
        self._flush()

    def delete(self, pid: str) -> None:
        self._items.pop(pid, None)
        for q in self._items.values():
            if q.jump_id == pid:
                q.jump_id = ""
        self._flush()

    def duplicate(self, pid: str) -> Optional[ConnectionProfile]:
        src = self._items.get(pid)
        if not src:
            return None
        dup = replace(src, id=new_id(), name=f"{src.name} copy", last_connected=0.0,
                      tags=list(src.tags))
        self._items[dup.id] = dup
        self._flush()
        return dup

    def touch(self, pid: str) -> None:
        if pid in self._items:
            self._items[pid].last_connected = time.time()
            self._flush()

    # ---- credentials ----
    def credentials(self, p: ConnectionProfile, vault: Vault,
                    secrets: Optional[Secrets] = None,
                    _seen: frozenset = frozenset()) -> Credentials:
        """Build Credentials (with jump chain). `secrets` overrides stored values
        field-by-field where not None."""
        if p.id in _seen:
            raise ConnectionConfigError("Jump host chain contains a loop.")
        seen = _seen | {p.id}

        def pick(override: Optional[str], token: str) -> Optional[str]:
            if override is not None:
                return override or None
            return vault.decrypt(token) if token else None

        s = secrets or Secrets()
        creds = Credentials(host=p.host, port=p.port, username=p.username)
        if p.auth == "password":
            creds.password = pick(s.password, p.password_enc)
        else:
            creds.private_key = pick(s.private_key, p.key_enc)
            creds.passphrase = pick(s.passphrase, p.passphrase_enc)
        if p.jump_id:
            j = self._items.get(p.jump_id)
            if j is None:
                raise ConnectionConfigError("The selected jump host no longer exists.")
            creds.jump = self.credentials(j, vault, None, seen)
        return creds

    # ---- import / export (encrypted with a user passphrase) ----
    def export_encrypted(self, vault: Vault, passphrase: str,
                         ids: Optional[list[str]] = None) -> str:
        out = []
        for p in self.list():
            if ids is not None and p.id not in ids:
                continue
            d = asdict(p)
            d["password"] = vault.decrypt(p.password_enc) if p.password_enc else ""
            d["private_key"] = vault.decrypt(p.key_enc) if p.key_enc else ""
            d["key_passphrase"] = vault.decrypt(p.passphrase_enc) if p.passphrase_enc else ""
            for k in ("password_enc", "key_enc", "passphrase_enc", "last_connected"):
                d.pop(k, None)
            out.append(d)
        payload = json.dumps({"v": 1, "profiles": out}).encode()
        return json.dumps(encrypt_with_passphrase(payload, passphrase))

    def import_encrypted(self, text: str, passphrase: str, vault: Vault) -> list[ConnectionProfile]:
        try:
            blob = json.loads(text.strip())
        except ValueError as e:
            raise ConnectionConfigError("That is not a valid TAKVPS export.") from e
        try:
            data = json.loads(decrypt_with_passphrase(blob, passphrase))
            raw = data["profiles"]
            idmap = {d["id"]: new_id() for d in raw}
            created: list[ConnectionProfile] = []
            for d in raw:
                p = ConnectionProfile(
                    name=d["name"], host=d["host"], username=d["username"],
                    port=int(d.get("port", 22)), auth=d.get("auth", "password"),
                    tags=list(d.get("tags", [])), color=d.get("color", "#3B82F6"),
                    jump_id=idmap.get(d.get("jump_id", ""), ""), id=idmap[d["id"]],
                    ai_context=d.get("ai_context", ""))
                if d.get("password"):
                    p.password_enc = vault.encrypt(d["password"])
                if d.get("private_key"):
                    p.key_enc = vault.encrypt(d["private_key"])
                if d.get("key_passphrase"):
                    p.passphrase_enc = vault.encrypt(d["key_passphrase"])
                created.append(p)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ConnectionConfigError("That export file is damaged or incompatible.") from e
        backup = dict(self._items)
        for p in created:
            self._items[p.id] = p
        try:
            for p in created:
                self.validate(p)
        except ConnectionConfigError:
            self._items = backup
            raise
        self._flush()
        return created
