"""Saved AI profiles. API keys are stored vault-encrypted, like SSH credentials."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Optional

from services.ai.providers import KINDS, ProviderConfig
from .connections import new_id
from .fsutil import atomic_write
from .vault import Vault


@dataclass
class AIProfile:
    name: str
    kind: str = "openai"
    base_url: str = ""
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 1024
    headers: dict = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    api_key_enc: str = ""


_FIELDS = {f.name for f in fields(AIProfile)}


class AIProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, AIProfile] = {}
        try:
            for d in json.loads(path.read_text("utf-8")).get("profiles", []):
                p = AIProfile(**{k: v for k, v in d.items() if k in _FIELDS})
                self._items[p.id] = p
        except (OSError, ValueError, TypeError):
            pass

    def list(self) -> list[AIProfile]:
        return sorted(self._items.values(), key=lambda p: p.name.lower())

    def get(self, pid: str) -> Optional[AIProfile]:
        return self._items.get(pid)

    def validate(self, p: AIProfile) -> None:
        if not p.name.strip():
            raise ValueError("Please enter a name.")
        if p.kind not in KINDS:
            raise ValueError("Unknown provider type.")
        if not p.model.strip():
            raise ValueError("Please enter a model name (use “Fetch models” to see what's available).")
        if p.base_url and not p.base_url.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://")
        if not (0 <= p.temperature <= 2):
            raise ValueError("Temperature must be between 0 and 2.")
        if not (16 <= p.max_tokens <= 200_000):
            raise ValueError("Max tokens must be between 16 and 200000.")

    def save(self, p: AIProfile, api_key: Optional[str] = None, vault: Optional[Vault] = None) -> None:
        """`api_key`: None = keep existing, "" = clear (local models), else replace."""
        self.validate(p)
        if api_key is not None:
            if vault is None:
                raise ValueError("A vault is required to store the key.")
            p.api_key_enc = vault.encrypt(api_key) if api_key else ""
        self._items[p.id] = p
        atomic_write(self.path, json.dumps({"profiles": [asdict(x) for x in self._items.values()]}, indent=2),
                     private=True)

    def delete(self, pid: str) -> None:
        self._items.pop(pid, None)
        atomic_write(self.path, json.dumps({"profiles": [asdict(x) for x in self._items.values()]}, indent=2),
                     private=True)

    def config(self, p: AIProfile, vault: Vault, key_override: Optional[str] = None) -> ProviderConfig:
        key = key_override if key_override is not None else (vault.decrypt(p.api_key_enc) if p.api_key_enc else "")
        return ProviderConfig(kind=p.kind, base_url=p.base_url, model=p.model, api_key=key,
                              temperature=p.temperature, max_tokens=p.max_tokens, headers=dict(p.headers))
