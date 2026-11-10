"""App-lock state (PIN hash, failures, timeout). The PIN itself is never stored."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from services import applock as A
from .fsutil import atomic_write


class AppLockStore:
    def __init__(self, path: Path, iterations: int = A.ITERATIONS) -> None:
        self.path, self.iterations = path, iterations
        self.d = {"enabled": False, "salt": "", "hash": "", "failures": 0, "locked_until": 0.0, "timeout": 60}
        try:
            self.d.update(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        atomic_write(self.path, json.dumps(self.d), private=True)

    @property
    def enabled(self) -> bool:
        return bool(self.d["enabled"] and self.d["hash"])

    @property
    def timeout(self) -> int:
        return int(self.d["timeout"])

    def set_timeout(self, seconds: int) -> None:
        self.d["timeout"] = max(0, int(seconds))
        self._save()

    def enable(self, pin: str) -> Optional[str]:
        if (err := A.valid_pin(pin)):
            return err
        salt = A.new_salt()
        self.d.update(enabled=True, salt=salt, hash=A.hash_pin(pin, salt, self.iterations), failures=0, locked_until=0.0)
        self._save()
        return None

    def check(self, pin: str, now: Optional[float] = None) -> tuple[bool, int]:
        """-> (ok, seconds_to_wait). Wrong PINs increase the delay."""
        now = now if now is not None else time.time()
        if now < self.d["locked_until"]:
            return False, int(self.d["locked_until"] - now) + 1
        if self.enabled and A.verify(pin, self.d["salt"], self.d["hash"], self.iterations):
            self.d.update(failures=0, locked_until=0.0)
            self._save()
            return True, 0
        self.d["failures"] += 1
        wait = A.lockout_seconds(self.d["failures"])
        self.d["locked_until"] = now + wait if wait else 0.0
        self._save()
        return False, wait

    def disable(self, pin: str) -> bool:
        ok, _ = self.check(pin)
        if ok:
            self.d.update(enabled=False, salt="", hash="")
            self._save()
        return ok
