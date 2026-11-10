"""SSH local port-forward specs and favorites. Pure."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from services.snippets import new_id

_HOST = re.compile(r"^(?!-)[A-Za-z0-9._-]{1,253}$|^[0-9A-Fa-f:]{2,45}$")


@dataclass
class TunnelSpec:
    name: str
    remote_port: int
    remote_host: str = "127.0.0.1"          # as seen FROM the server
    local_port: int = 0                      # 0 = pick a free port on the phone
    id: str = field(default_factory=new_id)

    @property
    def label(self) -> str:
        return self.name or f"{self.remote_host}:{self.remote_port}"


def validate(s: TunnelSpec) -> Optional[str]:
    if not (1 <= s.remote_port <= 65535):
        return "Remote port must be between 1 and 65535."
    if not _HOST.match(s.remote_host.strip()):
        return "Enter a valid remote host (as the server sees it, e.g. 127.0.0.1 or db.internal)."
    if s.local_port != 0 and not (1024 <= s.local_port <= 65535):
        return "Local port must be 0 (automatic) or between 1024 and 65535."
    return None


class FavoritesStore:
    """Saved tunnels per connection id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, list[dict]] = {}
        try:
            self.data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        from storage.fsutil import atomic_write
        atomic_write(self.path, json.dumps(self.data, indent=1))

    def list(self, conn_id: str) -> list[TunnelSpec]:
        out = []
        for d in self.data.get(conn_id, []):
            try:
                out.append(TunnelSpec(**{k: d[k] for k in ("name", "remote_port", "remote_host", "local_port", "id") if k in d}))
            except TypeError:
                continue
        return out

    def add(self, conn_id: str, spec: TunnelSpec) -> None:
        items = [d for d in self.data.get(conn_id, []) if d.get("id") != spec.id]
        items.append(asdict(spec))
        self.data[conn_id] = items
        self._save()

    def remove(self, conn_id: str, spec_id: str) -> None:
        self.data[conn_id] = [d for d in self.data.get(conn_id, []) if d.get("id") != spec_id]
        self._save()
