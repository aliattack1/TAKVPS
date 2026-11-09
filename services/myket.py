"""Myket store integration intents.

Myket requires apps to use Myket intents for store-related actions
(check update, open app page, rate/review, show developer apps).
This module provides the URI schemes and a launcher that works on Android.
"""
from __future__ import annotations

# The app's package name on Myket.
APP_PACKAGE = "dev.takvps.takvps"

# Myket intent URI schemes.
CHECK_UPDATE = f"myket://check-update?id={APP_PACKAGE}"
APP_DETAILS = f"myket://details?id={APP_PACKAGE}"
APP_DOWNLOAD = f"myket://download/{APP_PACKAGE}"
APP_COMMENT = f"myket://comment?id={APP_PACKAGE}"
DEVELOPER_APPS = f"myket://developer/{APP_PACKAGE}"


def launch_myket_uri(page, uri: str) -> bool:
    """Launch a Myket intent URI. Returns True if launched, False on error."""
    try:
        r = page.launch_url(uri)
        if hasattr(r, "__await__"):
            import asyncio
            asyncio.ensure_future(r)
        return True
    except Exception:
        return False


def check_for_update(page) -> bool:
    """Open Myket's update check dialog."""
    return launch_myket_uri(page, CHECK_UPDATE)


def open_app_page(page) -> bool:
    """Open the app's Myket details page."""
    return launch_myket_uri(page, APP_DETAILS)


def open_rating(page) -> bool:
    """Open the Myket rating/review flow."""
    return launch_myket_uri(page, APP_COMMENT)


def show_developer_apps(page) -> bool:
    """Show all apps by the developer on Myket."""
    return launch_myket_uri(page, DEVELOPER_APPS)
