"""Per-connection chat history (local JSON files)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from services.ai.chat import Msg
from .fsutil import atomic_write

MAX_MESSAGES = 200


class ChatStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, conn_id: str) -> Path:
        return self.dir / (re.sub(r"[^A-Za-z0-9_-]", "_", conn_id or "none") + ".json")

    def load(self, conn_id: str) -> tuple[list[Msg], str]:
        try:
            d = json.loads(self._path(conn_id).read_text("utf-8"))
            return [Msg.from_dict(m) for m in d.get("messages", [])], d.get("mode", "")
        except (OSError, ValueError, TypeError):
            return [], ""

    def save(self, conn_id: str, msgs: list[Msg], mode: str) -> None:
        atomic_write(self._path(conn_id), json.dumps(
            {"mode": mode, "messages": [m.to_dict() for m in msgs[-MAX_MESSAGES:]]}), private=True)

    def clear(self, conn_id: str) -> None:
        try:
            self._path(conn_id).unlink()
        except FileNotFoundError:
            pass


def export_text(msgs: list[Msg]) -> str:
    out = []
    for m in msgs:
        who = {"user": "You", "assistant": "AI", "result": f"Command ({'AI' if m.ai else 'you'}): {m.cmd}"}[m.role]
        out.append(f"## {who}\n{m.text}\n")
    return "\n".join(out)
