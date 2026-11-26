"""Shared URL-launching helper for Flet 1.x.

Flet 1.x removed ``page.launch_url()`` (it was a method on ``Page`` in
Flet 0.x). The replacement is the ``ft.UrlLauncher`` *service*, which
must be added to ``page.services`` and ``page.update()`` called before
its async ``launch_url(url)`` method will work.

This module caches one ``UrlLauncher`` per page so callers don't have
to repeat the registration dance.
"""
from __future__ import annotations

from typing import Any


def _get_launcher(page: Any):
    """Return a cached ``ft.UrlLauncher`` service attached to the page."""
    cached = getattr(page, "_url_launcher", None)
    if cached is not None:
        return cached
    import flet as ft
    launcher = ft.UrlLauncher()
    try:
        page.services.append(launcher)
        page.update()
    except Exception:  # noqa: BLE001
        pass
    try:
        page._url_launcher = launcher  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return launcher


async def can_launch_url(page: Any, url: str) -> bool:
    """Return True if the device has an app registered to handle ``url``."""
    launcher = _get_launcher(page)
    try:
        return await launcher.can_launch_url(url)
    except Exception:  # noqa: BLE001
        return False


async def launch_url(page: Any, url: str) -> None:
    """Launch ``url`` in the system browser/app.

    Raises ``RuntimeError`` if the underlying ``UrlLauncher.launch_url``
    call raises — callers should catch and toast a friendly message.
    """
    launcher = _get_launcher(page)
    await launcher.launch_url(url)
