"""(D2) — Local-model-first routing.

When the user has both a local AI profile (Ollama / LM Studio / vLLM on the
same network) and a cloud profile, this module picks the local one when the
device is on the same network as the server, and falls back to the cloud
otherwise. Saves API costs for daily-driver use.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


def is_local_profile(profile) -> bool:
    """True if the profile's base_url points at a local/LAN address."""
    from services.ai.chat import is_local_url
    url = getattr(profile, "base_url", "") or ""
    return is_local_url(url)


def pick_best_profile(profiles: list, prefer_local: bool = True) -> Optional[object]:
    """Pick the best AI profile: local first if prefer_local, else the first one.

    `profiles` is a list of AIProfile objects (with .id, .name, .base_url, .kind).
    Returns the chosen profile, or None if the list is empty.
    """
    if not profiles:
        return None
    if not prefer_local:
        return profiles[0]
    local = [p for p in profiles if is_local_profile(p)]
    if local:
        return local[0]
    return profiles[0]


def should_try_local(active_profile, server_host: str = "") -> bool:
    """Heuristic: should we try the local model for this server?

    Returns True when the active profile is local AND the server is on a
    private network (so the round-trip is fast). This is a cheap heuristic
    — we don't actually probe the network, we just check if the server's
    host looks private (RFC 1918 / loopback / .local).
    """
    if not is_local_profile(active_profile):
        return False
    if not server_host:
        return True     # no server host to check — assume local is fine
    from services.ai.chat import is_local_url
    return is_local_url(server_host)
