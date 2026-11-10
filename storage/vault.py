"""Encryption at rest for credentials.

Design
------
* One random 256-bit *master key* encrypts every stored secret (Fernet = AES-128-CBC
  + HMAC-SHA256, from `cryptography`, which Flet ships for Android).
* Mode "device" (default): the master key lives in an owner-only file in the app's
  private storage. Protects against secrets leaking via exports/backups/logs and
  other apps (Android sandbox); it does NOT protect against someone who can read
  the app's private data directory (rooted phone / adb backup).
* Mode "passphrase": the master key is wrapped with a key derived from the user's
  passphrase (scrypt) and the key file is removed. Without the passphrase the
  secrets cannot be decrypted. Switching modes only re-wraps the master key.
* Exports are encrypted separately with a user-chosen passphrase (same scheme).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .fsutil import atomic_write

SCRYPT_N = 2 ** 15
_MAX_N = 2 ** 20


class VaultError(Exception):
    pass


class VaultLocked(VaultError):
    pass


def _derive(passphrase: str, salt: bytes, n: int) -> bytes:
    if n > _MAX_N:
        raise VaultError("Unsupported key-derivation parameters.")
    try:
        kdf = Scrypt(salt=salt, length=32, n=n, r=8, p=1)
        return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    except ValueError as e:
        raise VaultError("Unsupported key-derivation parameters.") from e


def encrypt_with_passphrase(data: bytes, passphrase: str) -> dict:
    salt = os.urandom(16)
    token = Fernet(_derive(passphrase, salt, SCRYPT_N)).encrypt(data).decode()
    return {"v": 1, "kdf": "scrypt", "n": SCRYPT_N,
            "salt": base64.b64encode(salt).decode(), "data": token}


def decrypt_with_passphrase(blob: dict, passphrase: str) -> bytes:
    try:
        salt = base64.b64decode(blob["salt"])
        key = _derive(passphrase, salt, int(blob["n"]))
        return Fernet(key).decrypt(blob["data"].encode())
    except InvalidToken as e:
        raise VaultError("Wrong passphrase or corrupted file.") from e
    except (KeyError, TypeError, ValueError) as e:
        raise VaultError("Not a valid TAKVPS export.") from e


class Vault:
    def __init__(self, directory: Path) -> None:
        self._meta = directory / "vault.json"
        self._keyfile = directory / "vault.key"
        self._master: Optional[bytes] = None
        self._f: Optional[Fernet] = None
        self.mode = "device"
        self._meta_data: dict = {}
        self._load()

    # ---- state ----
    @property
    def locked(self) -> bool:
        return self._f is None

    def _set_master(self, master: bytes) -> None:
        self._master = master
        self._f = Fernet(master)

    def _load(self) -> None:
        if self._meta.exists():
            try:
                self._meta_data = json.loads(self._meta.read_text("utf-8"))
            except ValueError as e:
                raise VaultError("Vault metadata is corrupted.") from e
            self.mode = self._meta_data.get("mode", "device")
        if self.mode == "passphrase":
            return                                   # stays locked until unlock()
        if self._keyfile.exists():
            self._set_master(self._keyfile.read_text("utf-8").strip().encode())
        elif self._meta.exists():
            raise VaultError("The vault key file is missing; stored credentials cannot be read.")
        else:
            master = Fernet.generate_key()
            atomic_write(self._keyfile, master.decode(), private=True)
            self._set_master(master)
        if not self._meta.exists():
            self._write_meta({"mode": "device"})

    def _write_meta(self, meta: dict) -> None:
        self._meta_data = meta
        self.mode = meta["mode"]
        atomic_write(self._meta, json.dumps(meta), private=True)

    # ---- passphrase mode ----
    def unlock(self, passphrase: str) -> bool:
        if self.mode != "passphrase":
            return True
        try:
            salt = base64.b64decode(self._meta_data["salt"])
            kek = _derive(passphrase, salt, int(self._meta_data["n"]))
            master = Fernet(kek).decrypt(self._meta_data["wrapped"].encode())
        except (InvalidToken, KeyError, ValueError):
            return False
        self._set_master(master)
        return True

    def lock(self) -> None:
        if self.mode == "passphrase":
            self._master = self._f = None

    def enable_passphrase(self, passphrase: str) -> None:
        """Enable (or change) passphrase protection. Vault must be unlocked."""
        if self._master is None:
            raise VaultLocked("Vault is locked.")
        salt = os.urandom(16)
        kek = _derive(passphrase, salt, SCRYPT_N)
        self._write_meta({"mode": "passphrase", "n": SCRYPT_N,
                          "salt": base64.b64encode(salt).decode(),
                          "wrapped": Fernet(kek).encrypt(self._master).decode()})
        try:
            self._keyfile.unlink()
        except FileNotFoundError:
            pass

    def disable_passphrase(self) -> None:
        if self._master is None:
            raise VaultLocked("Vault is locked.")
        atomic_write(self._keyfile, self._master.decode(), private=True)
        self._write_meta({"mode": "device"})

    # ---- data ----
    def encrypt(self, plaintext: str) -> str:
        if self._f is None:
            raise VaultLocked("Vault is locked.")
        return self._f.encrypt(plaintext.encode("utf-8")).decode()

    def decrypt(self, token: str) -> str:
        if self._f is None:
            raise VaultLocked("Vault is locked.")
        try:
            return self._f.decrypt(token.encode()).decode("utf-8")
        except InvalidToken as e:
            raise VaultError("Could not decrypt stored data (wrong key or corrupted).") from e
