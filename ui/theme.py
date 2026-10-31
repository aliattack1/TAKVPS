"""Design tokens and theme builder."""
from __future__ import annotations

import flet as ft

# preset -> (seed, dark surface override or None)
PRESETS: dict[str, tuple[str, str | None]] = {
    "default": ("#3B82F6", None),
    "midnight": ("#6366F1", "#0B1020"),
    "amoled": ("#22C55E", "#000000"),
    "nord": ("#88C0D0", "#2E3440"),
    "dracula": ("#BD93F9", "#282A36"),
    "solarized": ("#B58900", "#002B36"),
}

SPACE = 12
RADIUS = 16
MIN_TAP = 44
PHONE_MAX_WIDTH = 700


def apply(page: ft.Page, mode: str, preset: str, accent: str) -> None:
    seed, dark_surface = PRESETS.get(preset, PRESETS["default"])
    if preset == "default":
        seed = accent
    page.theme = ft.Theme(color_scheme_seed=seed)
    if dark_surface:
        page.dark_theme = ft.Theme(color_scheme_seed=seed,
                                   color_scheme=ft.ColorScheme(surface=dark_surface))
    else:
        page.dark_theme = ft.Theme(color_scheme_seed=seed)
    page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(
        mode, ft.ThemeMode.SYSTEM)
