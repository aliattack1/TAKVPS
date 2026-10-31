"""Local activity log of commands the app ran on servers (JSON lines).

each record now carries a `prev_hash` and `hash` field
forming a tamper-evident chain (see `services/audit_chain.py`). Editing a
single record in the file breaks the chain and is detected by `verify()`.
Old records (pre-Phase-14-Part-7) don't have hash fields and are reported
as "unverified" (not tampered, just old).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from services.audit_chain import GENESIS, add_chain_fields

MAX_LINES = 3000
KEEP = 2000


class ActivityLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_hash: str = GENESIS     # cached; re-read from file on first add

    def _read_last_hash(self) -> str:
        """Read the hash of the last line in the file, or GENESIS if empty."""
        try:
            lines = self.path.read_text("utf-8").splitlines()
            if not lines:
                return GENESIS
            last = json.loads(lines[-1])
            return last.get("hash", "") or GENESIS
        except (OSError, ValueError):
            return GENESIS

    def add(self, conn_id: str, conn_name: str, command: str, exit_code: int, source: str, risk: str) -> None:
        """`source`: "ai" for AI-initiated commands, "user" otherwise. Commands are redacted by callers."""
        if self._last_hash == GENESIS and self.path.exists():
            # First add in this session — read the last hash from the file.
            self._last_hash = self._read_last_hash()
        rec = {"ts": time.time(), "conn": conn_id, "name": conn_name, "cmd": command[:2000],
               "exit": exit_code, "source": source, "risk": risk}
        # add chained hash fields for tamper-evidence.
        rec = add_chain_fields(rec, self._last_hash)
        self._last_hash = rec["hash"]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        try:
            lines = self.path.read_text("utf-8").splitlines()
            if len(lines) > MAX_LINES:
                self.path.write_text("\n".join(lines[-KEEP:]) + "\n", "utf-8")
                # After trimming, re-read the last hash (it may have changed).
                self._last_hash = self._read_last_hash()
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._last_hash = GENESIS

    def recent(self, n: int = 100) -> list[dict]:
        try:
            lines = self.path.read_text("utf-8").splitlines()[-n:]
        except OSError:
            return []
        out = []
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out

    def verify(self) -> list:
        """verify the hash chain across all records.

        Returns a list of `audit_chain.VerifyResult` objects, one per record.
        Use `audit_chain.summary_text(results)` for a one-line status, or
        `audit_chain.is_log_intact(results)` for a boolean.
        """
        from services.audit_chain import verify_records
        try:
            lines = self.path.read_text("utf-8").splitlines()
        except OSError:
            return []
        records = []
        for ln in lines:
            try:
                records.append(json.loads(ln))
            except ValueError:
                # A corrupt JSON line is itself a form of tampering.
                records.append({"_corrupt": True, "_raw": ln[:200]})
        return verify_records(records)
