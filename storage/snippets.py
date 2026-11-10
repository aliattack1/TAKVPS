"""Snippet / deploy storage and run history (local JSON)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Optional

from services.deploy import Deploy
from services.snippets import Snippet, new_id
from .fsutil import atomic_write

_SN = {f.name for f in fields(Snippet)}
_DP = {f.name for f in fields(Deploy)}


class SnippetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.snippets: dict[str, Snippet] = {}
        self.deploys: dict[str, Deploy] = {}
        try:
            raw = json.loads(path.read_text("utf-8"))
            for d in raw.get("snippets", []):
                s = Snippet(**{k: v for k, v in d.items() if k in _SN})
                self.snippets[s.id] = s
            for d in raw.get("deploys", []):
                x = Deploy(**{k: v for k, v in d.items() if k in _DP})
                self.deploys[x.id] = x
        except (OSError, ValueError, TypeError):
            pass

    def _flush(self) -> None:
        atomic_write(self.path, json.dumps({"snippets": [asdict(s) for s in self.snippets.values()],
                                            "deploys": [asdict(d) for d in self.deploys.values()]}, indent=1))

    @staticmethod
    def _visible(scope: str, conn_id: str) -> bool:
        return scope in ("", conn_id)

    # ---- snippets ----
    def list_snippets(self, conn_id: str = "") -> list[Snippet]:
        items = [s for s in self.snippets.values() if self._visible(s.scope, conn_id)]
        return sorted(items, key=lambda s: (s.group.lower(), s.order, s.name.lower()))

    def save_snippet(self, s: Snippet) -> None:
        if s.id not in self.snippets:
            s.order = max((x.order for x in self.snippets.values() if x.group == s.group), default=-1) + 1
        self.snippets[s.id] = s
        self._flush()

    def delete_snippet(self, sid: str) -> None:
        self.snippets.pop(sid, None)
        self._flush()

    def duplicate_snippet(self, sid: str) -> Optional[Snippet]:
        src = self.snippets.get(sid)
        if not src:
            return None
        dup = replace(src, id=new_id(), name=f"{src.name} copy")
        self.save_snippet(dup)
        return dup

    def move_snippet(self, sid: str, delta: int, conn_id: str = "") -> None:
        s = self.snippets.get(sid)
        if not s:
            return
        peers = sorted((x for x in self.snippets.values() if x.group == s.group and self._visible(x.scope, conn_id)),
                       key=lambda x: (x.order, x.name.lower()))
        for i, x in enumerate(peers):                      # normalise to 0..n-1 first
            x.order = i
        i = peers.index(s)
        j = i + delta
        if 0 <= j < len(peers):
            peers[i].order, peers[j].order = peers[j].order, peers[i].order
        self._flush()

    def groups(self) -> list[str]:
        return sorted({s.group for s in self.snippets.values() if s.group})

    # ---- deploys ----
    def list_deploys(self, conn_id: str = "") -> list[Deploy]:
        return sorted((d for d in self.deploys.values() if self._visible(d.scope, conn_id)),
                      key=lambda d: d.name.lower())

    def save_deploy(self, d: Deploy) -> None:
        self.deploys[d.id] = d
        self._flush()

    def delete_deploy(self, did: str) -> None:
        self.deploys.pop(did, None)
        self._flush()

    def add_many(self, snippets: list[Snippet], deploys: list[Deploy], scope: str = "") -> None:
        for s in snippets:
            s.id, s.scope = new_id(), scope
            self.save_snippet(s)
        for d in deploys:
            d.id, d.scope = new_id(), scope
            self.deploys[d.id] = d
        self._flush()


class RunHistory:
    MAX = 500

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, kind: str, item_id: str, name: str, conn: str, exit_code: int, seconds: float, output: str) -> None:
        rec = {"ts": time.time(), "kind": kind, "item": item_id, "name": name, "conn": conn,
               "exit": exit_code, "sec": round(seconds, 1), "out": output[-4000:]}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        try:
            lines = self.path.read_text("utf-8").splitlines()
            if len(lines) > self.MAX:
                self.path.write_text("\n".join(lines[-self.MAX // 2:]) + "\n", "utf-8")
        except OSError:
            pass

    def recent(self, n: int = 50, item_id: str = "") -> list[dict]:
        try:
            lines = self.path.read_text("utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in reversed(lines):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if not item_id or d.get("item") == item_id:
                out.append(d)
                if len(out) >= n:
                    break
        return out
