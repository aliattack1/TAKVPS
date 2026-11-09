"""Web mode documentation.

Flet can serve the app over HTTP. This module provides the configuration
and security notes for running TAKVPS in web mode.

⚠️ SECURITY WARNING: web mode exposes the SSH credentials vault over HTTP.
Only enable it on a trusted local network, never on a public server.

To run in web mode:
    flet run --web main.py

The app will be available at http://localhost:8000 (or the specified port).
On Android, web mode is NOT available — use the APK build instead.

Limitations in web mode:
  - No `page.on_drop` (drag-and-drop) — use the Upload button.
  - No `page.on_keyboard_event` (hardware keyboard) — use the on-screen toolbar.
  - No biometrics / app lock — the PIN dialog still works but there's no
    OS-level screen lock integration.
  - File downloads go to the browser's download folder, not the app's private
    storage.
  - The `flet-charts` package may not render in all browsers; the bar
    sparkline fallback is used automatically.
"""
from __future__ import annotations

WEB_MODE_LIMITATIONS = [
    "No drag-and-drop file upload — use the Upload button instead.",
    "No hardware-keyboard shortcuts — use the on-screen toolbar.",
    "No app lock / biometrics — the PIN dialog works but there's no OS-level lock.",
    "File downloads go to the browser's download folder.",
    "Line charts may not render in all browsers — bar sparkline fallback is used.",
    "Mouse reporting (vim/less/tmux clicks) doesn't work — taps aren't forwarded.",
    "Terminal input uses a text field (same as mobile), not direct keyboard events.",
]

WEB_MODE_SECURITY_NOTES = [
    "⚠️ Web mode exposes the SSH credentials vault over HTTP. Only run on a trusted local network.",
    "⚠️ Never expose the web port to the internet without HTTPS + authentication.",
    "The vault encryption still applies, but the decrypted credentials are in memory while the app runs.",
    "For production use, wrap the web server in a reverse proxy with TLS (nginx/Caddy).",
]

CAN_RUN_WEB = True     # Flet supports web mode


def web_mode_summary() -> str:
    """One-paragraph summary for the Settings page."""
    return ("Web mode serves the app over HTTP (flet run --web main.py). "
            "⚠️ Only use on a trusted local network — the SSH credentials vault is in memory. "
            f"{len(WEB_MODE_LIMITATIONS)} limitations apply (drag-drop, keyboard, biometrics, etc.).")
