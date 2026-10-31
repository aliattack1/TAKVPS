"""Database login settings per server and engine. Passwords are vault-encrypted."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from services.dbs import Auth
from .fsutil import atomic_write
from .vault import Vault


class DbProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {}
        try:
            self.data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            pass

    def get(self, conn_id: str, engine: str, vault: Optional[Vault]) -> Auth:
        d = dict(self.data.get(conn_id, {}).get(engine, {}))
        enc = d.pop("password_enc", "")
        a = Auth(**{k: v for k, v in d.items() if k in ("mode", "user", "host", "port", "path", "sudo")})
        if enc and vault is not None and not vault.locked:
            try:
                a.password = vault.decrypt(enc)
            except Exception:  # noqa: BLE001
                a.password = ""
        return a

    def has_password(self, conn_id: str, engine: str) -> bool:
        return bool(self.data.get(conn_id, {}).get(engine, {}).get("password_enc"))

    def save(self, conn_id: str, engine: str, auth: Auth, vault: Vault, password: Optional[str]) -> None:
        """password: None = keep the stored one, "" = clear, else replace."""
        cur = self.data.setdefault(conn_id, {}).get(engine, {})
        d = {k: v for k, v in asdict(auth).items() if k != "password"}
        d["password_enc"] = cur.get("password_enc", "") if password is None else (vault.encrypt(password) if password else "")
        self.data[conn_id][engine] = d
        atomic_write(self.path, json.dumps(self.data, indent=1), private=True)
