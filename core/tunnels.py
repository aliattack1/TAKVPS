"""Runtime manager for active SSH local port forwards (always bound to 127.0.0.1)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from services.tunnels import TunnelSpec, validate


@dataclass
class ActiveTunnel:
    spec: TunnelSpec
    port: int                       # the port actually listening on the phone
    listener: Any = field(repr=False, default=None)
    started: float = field(default_factory=time.time)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"


class TunnelManager:
    def __init__(self, ssh: Any) -> None:
        self.ssh = ssh
        self.active: dict[str, ActiveTunnel] = {}

    async def start(self, spec: TunnelSpec) -> ActiveTunnel:
        if (err := validate(spec)):
            raise ValueError(err)
        if spec.id in self.active:
            return self.active[spec.id]
        listener = await self.ssh.forward(spec.local_port, spec.remote_host.strip(), spec.remote_port)
        t = ActiveTunnel(spec, listener.get_port(), listener)
        self.active[spec.id] = t
        return t

    def stop(self, spec_id: str) -> None:
        t = self.active.pop(spec_id, None)
        if t and t.listener is not None:
            try:
                t.listener.close()
            except Exception:  # noqa: BLE001
                pass

    def stop_all(self) -> None:
        for sid in list(self.active):
            self.stop(sid)
