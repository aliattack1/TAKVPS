"""Myket store integration intents.

Myket requires apps to use Myket intents for store-related actions
(check update, open app page, rate/review, show developer apps).

This module uses the shared ``services.urlutil`` helper which wraps
Flet 1.x's ``ft.UrlLauncher`` service. The old ``page.launch_url()``
method was removed in Flet 1.x — that was the root cause of the
buttons doing nothing on Android (the call raised ``AttributeError``
which was silently swallowed).

Strategy: try the ``myket://`` intent URI first (opens the Myket app
if installed). If the device reports it can't handle the scheme, fall
back to the Myket web URL ``https://myket.ir/app/<package>`` which opens
in any browser and uses Android App Links to redirect to the Myket app
if it IS installed — so the buttons always do *something* visible.
"""
from __future__ import annotations

from typing import Any

from services.urlutil import can_launch_url, launch_url

# The app's package name on Myket.
APP_PACKAGE = "dev.takvps.takvps"

# Myket intent URI schemes (open in the Myket app if installed).
CHECK_UPDATE = f"myket://check-update?id={APP_PACKAGE}"
APP_DETAILS = f"myket://details?id={APP_PACKAGE}"
APP_DOWNLOAD = f"myket://download/{APP_PACKAGE}"
APP_COMMENT = f"myket://comment?id={APP_PACKAGE}"
DEVELOPER_APPS = f"myket://developer?id={APP_PACKAGE}"

# Myket web URLs (work in any browser; redirect to the Myket app via App Links).
WEB_APP_URL = f"https://myket.ir/app/{APP_PACKAGE}"


async def launch_myket_uri(page: Any, intent_uri: str, web_url: str = WEB_APP_URL) -> bool:
    """Launch a Myket intent URI, falling back to the web URL.

    Returns ``True`` if *anything* was launched (intent or browser),
    ``False`` only if both attempts raised an exception.
    """
    # 1) Try the myket:// intent. Use ``can_launch_url`` to detect whether
    #    the device has an app registered for the scheme — if it doesn't,
    #    calling ``launch_url`` would silently no-op on Android.
    if await can_launch_url(page, intent_uri):
        try:
            await launch_url(page, intent_uri)
            return True
        except Exception:  # noqa: BLE001
            pass  # fall through to web URL

    # 2) Fall back to the web URL. This always opens the browser, and
    #    Android App Links redirect to the Myket app if installed.
    try:
        await launch_url(page, web_url)
        return True
    except Exception:  # noqa: BLE001
        return False


async def check_for_update(page: Any) -> bool:
    """Open Myket's update check dialog (falls back to app page on web)."""
    return await launch_myket_uri(page, CHECK_UPDATE, WEB_APP_URL)


async def open_app_page(page: Any) -> bool:
    """Open the app's Myket details page."""
    return await launch_myket_uri(page, APP_DETAILS, WEB_APP_URL)


async def open_rating(page: Any) -> bool:
    """Open the Myket rating/review flow."""
    return await launch_myket_uri(page, APP_COMMENT, WEB_APP_URL)


async def show_developer_apps(page: Any) -> bool:
    """Show all apps by the developer on Myket."""
    return await launch_myket_uri(page, DEVELOPER_APPS, WEB_APP_URL)
