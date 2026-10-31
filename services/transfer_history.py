"""Persistent transfer history .

The transfer queue (services/transfers.py) keeps items in memory only, so the
history is lost when the app restarts. This module wraps that queue with a
small JSON-backed history that survives restarts and deduplicates against the
queue's current items so we don't double-record.

Pure logic: load()/save()/append() take only JSON-serialisable data. The actual
file IO is done in load()/save() with the existing fsutil.atomic_write helper.

Storage format (JSON Lines would have been nicer but JSON is simpler to read
back into a list):
    {
      "v": 1,
      "items": [
        {"id": 1, "kind": "down", "name": "...", "remote": "...", "local": "...",
         "total": 1234, "status": "done", "error": "", "created": 1730254881,
         "finished": 1730254890}
      ]
    }
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storage.fsutil import atomic_write

SCHEMA_VERSION = 1
DEFAULT_KEEP = 200           # cap so the file stays tiny forever


@dataclass
class HistoryItem:
    kind: str             # "down" | "up"
    name: str
    remote: str
    local: str
    total: int
    status: str           # services/transfers.py constants
    error: str = ""
    created: float = field(default_factory=time.time)
    finished: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "name": self.name, "remote": self.remote,
            "local": self.local, "total": self.total, "status": self.status,
            "error": self.error, "created": self.created, "finished": self.finished,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryItem":
        return cls(
            kind=d.get("kind", "?"), name=d.get("name", "?"),
            remote=d.get("remote", ""), local=d.get("local", ""),
            total=int(d.get("total", 0) or 0),
            status=d.get("status", "done"),
            error=d.get("error", "") or "",
            created=float(d.get("created", 0) or 0),
            finished=float(d.get("finished", 0) or 0),
        )


@dataclass
class TransferHistory:
    items: list[HistoryItem] = field(default_factory=list)
    keep: int = DEFAULT_KEEP

    def append(self, item: HistoryItem) -> None:
        self.items.append(item)
        # Cap: keep the most recent N items (newest first when sliced).
        if len(self.items) > self.keep:
            self.items = self.items[-self.keep:]

    def clear(self) -> None:
        self.items = []

    def filter(self, kind: Optional[str] = None) -> list[HistoryItem]:
        if not kind:
            return list(self.items)
        return [i for i in self.items if i.kind == kind]


# ---- file IO ----------------------------------------------------------------

def history_path(store_dir: Path) -> Path:
    return store_dir / "transfer_history.json"


def load(path: Path) -> TransferHistory:
    try:
        raw = json.loads(path.read_text("utf-8"))
        items = [HistoryItem.from_dict(d) for d in raw.get("items", [])]
        return TransferHistory(items=items)
    except (OSError, ValueError, TypeError):
        return TransferHistory()


def save(path: Path, history: TransferHistory) -> None:
    payload = {"v": SCHEMA_VERSION, "items": [i.to_dict() for i in history.items]}
    atomic_write(path, json.dumps(payload, indent=2), private=True)


def record_finished(t, history: TransferHistory, finished_at: Optional[float] = None) -> None:
    """Convert a Transfer (from services/transfers.py) into a HistoryItem and append."""
    finished = finished_at if finished_at is not None else time.time()
    history.append(HistoryItem(
        kind=t.kind,
        name=t.name,
        remote=t.remote,
        local=t.local,
        total=t.total,
        status=t.status,
        error=t.error or "",
        created=t.created,
        finished=finished,
    ))


def dedupe_against_queue(history: TransferHistory, queue_items: list) -> None:
    """Don't keep showing a transfer as both 'in queue' and 'in history'.

    Removes history items whose (kind, name, remote, local, total) tuple matches
    any active queue item. The active item will be added to history when it
    finishes.
    """
    active_keys = {
        (q.kind, q.name, q.remote, q.local, q.total) for q in queue_items
    }
    history.items = [
        h for h in history.items
        if (h.kind, h.name, h.remote, h.local, h.total) not in active_keys
    ]
