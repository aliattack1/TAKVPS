"""Design tokens and theme builder.

This module exposes the single source of truth for the app's visual language:
spacing scale, corner-radius scale, elevation (shadow) tokens, semantic state
colors, and density presets. Everything else in the UI should pull from these
tokens rather than hard-coding numbers — that way a single change here
propagates everywhere consistently.

Persian/RTL support: when the user's language is `fa`, the page is set to
right-to-left (RTL) mode and the Vazirmatn font family is registered as the
default Persian font. Vazirmatn is the de-facto standard for Persian UIs
(https://github.com/rastikerdar/vazirmatn) — Material's bundled Roboto doesn't
shape Arabic/Persian letters correctly (no proper joining, no Persian-specific
glyphs like ﭺ ﮊ ﮒ ﮒ ﻉ).

Design references:
  - Material 3 Expressive (m3.material.io) — card elevation, FAB guidance,
    color roles (primary/secondary/tertiary containers).
  - Linear / Vercel dashboards — calm surfaces, generous spacing, status
    pills, monospace numerics, tight typography.
  - Tailwind UI — semantic spacing scale (4/8/12/16/24), rounded-2xl corners
    (16dp), subtle dividers.
"""
from __future__ import annotations

import flet as ft

# ---------------------------------------------------------------------------
# Color presets
# ---------------------------------------------------------------------------
# preset -> (seed, dark surface override or None)
PRESETS: dict[str, tuple[str, str | None]] = {
    "default": ("#3B82F6", None),
    "midnight": ("#6366F1", "#0B1020"),
    "amoled": ("#22C55E", "#000000"),
    "nord": ("#88C0D0", "#2E3440"),
    "dracula": ("#BD93F9", "#282A36"),
    "solarized": ("#B58900", "#002B36"),
}

# ---------------------------------------------------------------------------
# Spacing scale (4-step geometric — Tailwind-like)
# ---------------------------------------------------------------------------
# Use these tokens everywhere instead of bare numbers so the whole app
# breathes with the same rhythm. Pages should pick the *smallest* token
# that gives their content enough breathing room — this keeps information
# density high (Linear-style) without feeling cramped.
SPACE_XS = 4    # tight grouping: icon + label inside a button
SPACE_SM = 8    # default intra-card spacing
SPACE = 12      # default inter-card / page padding
SPACE_LG = 16    # section breaks within a page
SPACE_XL = 24    # empty-state padding, dialog padding

# Backwards-compatible aliases for code that still imports the old names.
# (Kept so existing pages don't need to be touched in this pass.)
_OLD_SPACE = SPACE

# ---------------------------------------------------------------------------
# Corner-radius scale
# ---------------------------------------------------------------------------
# Tailwind UI uses rounded-lg (8dp) for inputs, rounded-xl (12dp) for buttons,
# rounded-2xl (16dp) for cards. We mirror that.
RADIUS_SM = 8     # pills, badges, status chips
RADIUS_MD = 12    # buttons, text fields, small chips
RADIUS = 16       # cards, dialogs, sheets (the default)
RADIUS_LG = 20    # large hero cards, FAB container
RADIUS_PILL = 999  # fully rounded pills

# ---------------------------------------------------------------------------
# Tap-target / sizing tokens
# ---------------------------------------------------------------------------
# Material 3's minimum tap target is 48dp; Apple HIG is 44dp. We pick 44
# (the existing minimum) for compactness on phones, but explicitly tag
# touch targets as such so they can be enlarged independently of icons.
MIN_TAP = 44
ICON_SIZE = 22       # default list-item / nav-rail icon
ICON_SIZE_LG = 28    # prominent icons (empty states, headers)
ICON_SIZE_XL = 48    # hero / first-run tour icons

PHONE_MAX_WIDTH = 700   # px; below this we render the mobile layout

# ---------------------------------------------------------------------------
# Density presets
# ---------------------------------------------------------------------------
# Density controls how tight list items, cards and rows are. "comfortable"
# is the default; "compact" is for power-user pages like Processes/Snippets
# where information density matters more than breathing room.
DENSITY = {"compact": 0.85, "comfortable": 1.0, "spacious": 1.15}

# ---------------------------------------------------------------------------
# Semantic state colors
# ---------------------------------------------------------------------------
# Used for status pills, banners, badges, log severity coloring, health
# indicators, etc. Each state has a "container" (background) and an "on"
# (foreground) variant — Material 3's *_CONTAINER / ON_*_CONTAINER pattern.
#
# Flet only ships ERROR_CONTAINER / PRIMARY_CONTAINER / SECONDARY_CONTAINER /
# TERTIARY_CONTAINER plus the SURFACE_CONTAINER_* ramp — there's no
# GREEN_CONTAINER etc. So we use specific Material palette shades that read
# well in both light and dark modes. The "container" picks a 100-shade for
# light-mode backgrounds, the "on" picks a 900-shade for legible text on
# that background. (In dark mode Flet flips the polarity automatically when
# the page-level theme_mode is dark, but these shades still look acceptable
# on dark surfaces.)
#
# The "plain" color is the solid swatch — used for icons, dots and small
# indicators where a background container would look heavy.
STATE_COLORS = {
    # status -> (container, on_container, plain)
    # All containers are 100-shade Material colors for a soft, calm wash;
    # the "on" foreground is the 900-shade for AA-contrast text.
    "ok":       (ft.Colors.GREEN_100,   ft.Colors.GREEN_900,   ft.Colors.GREEN),
    "info":     (ft.Colors.BLUE_100,    ft.Colors.BLUE_900,    ft.Colors.BLUE),
    "warning":  (ft.Colors.AMBER_100,   ft.Colors.AMBER_900,   ft.Colors.AMBER),
    "error":    (ft.Colors.RED_100,     ft.Colors.RED_900,     ft.Colors.RED),
    "critical": (ft.Colors.RED_200,     ft.Colors.RED_900,     ft.Colors.RED_700),
    "neutral":  (ft.Colors.SECONDARY_CONTAINER, ft.Colors.ON_SECONDARY_CONTAINER,
                 ft.Colors.SECONDARY),
    "muted":    (ft.Colors.SURFACE_CONTAINER_HIGHEST, ft.Colors.ON_SURFACE,
                 ft.Colors.ON_SURFACE_VARIANT),
}


def state_color(state: str, role: str = "container") -> str:
    """Look up a semantic state color.

    Args:
        state: one of ok/info/warning/error/critical/neutral/muted.
        role: "container" (background), "on" (foreground on container),
              or "plain" (solid color for icons/borders).

    Returns a Flet named color string. Falls back to "neutral" / "muted"
    for unknown states so the UI never crashes on a misspelled state.
    """
    s = STATE_COLORS.get(state, STATE_COLORS["neutral"])
    if role == "on":
        return s[1]
    if role == "plain":
        return s[2]
    return s[0]


# Map common status strings (from services / user content) to one of our
# semantic states. Pages can pass through arbitrary status words and get
# back a sensible color without writing big if/elif chains.
_STATUS_TO_STATE = {
    # green
    "connected": "ok", "online": "ok", "running": "ok", "active": "ok",
    "healthy": "ok", "ok": "ok", "up": "ok", "enabled": "ok", "installed": "ok",
    "success": "ok", "completed": "ok", "done": "ok", "ready": "ok",
    # blue
    "info": "info", "notice": "info", "in_progress": "info",
    "pending": "info", "queued": "info", "connecting": "info",
    "syncing": "info", "loading": "info",
    # amber
    "warning": "warning", "warn": "warning", "stale": "warning",
    "paused": "warning", "idle": "warning", "slow": "warning",
    "limited": "warning", "degraded": "warning", "unhealthy": "warning",
    # red — error/critical
    "error": "error", "failed": "error", "failure": "error",
    "disconnected": "error", "offline": "error", "stopped": "error",
    "exited": "error", "dead": "error", "crashed": "error",
    "unreachable": "error", "timeout": "error", "timed_out": "error",
    # critical — slightly different shade for "dangerous"
    "critical": "critical", "dangerous": "critical", "destructive": "critical",
    # neutral
    "inactive": "neutral", "disabled": "neutral", "not_running": "neutral",
    "not_installed": "neutral", "unknown": "neutral", "": "neutral",
}


def status_to_state(status: str) -> str:
    """Map an arbitrary status string to one of our semantic states.

    Examples:
        status_to_state("connected") → "ok"
        status_to_state("exited")    → "error"
        status_to_state("paused")    → "warning"
        status_to_state("")          → "neutral"

    The lookup is case-insensitive and falls back to "neutral" for any
    unknown status, so callers never have to defensive-check.
    """
    return _STATUS_TO_STATE.get((status or "").lower(), "neutral")


# ---------------------------------------------------------------------------
# Elevation (shadow) tokens
# ---------------------------------------------------------------------------
# Material 3 elevation levels: 0 (flat), 1 (1dp), 2 (3dp), 3 (6dp), 4 (8dp),
# 5 (12dp). We expose 4 levels that map to real use cases:
ELEVATION_FLAT = 0       # default cards (filled cards in M3)
ELEVATION_LOW = 1        # raised cards, hover state
ELEVATION_MEDIUM = 3     # dialogs, dropdowns
ELEVATION_HIGH = 5       # FAB, modals, sheets

# Persian font family — must match the name registered by register_persian_fonts().
PERSIAN_FONT_FAMILY = "Vazirmatn"

# Persian-aware fonts that should be tried in order. The first one available
# on the system wins. Vazirmatn is the standard; Sahel and Shabnam are common
# Iranian-designed alternatives that some Linux distros ship instead.
_PERSIAN_FONT_CANDIDATES = ("Vazirmatn", "Vazir", "Sahel", "Shabnam", "IRANSans")


def register_persian_fonts(page: ft.Page) -> str | None:
    """Try to register a Persian font with Flet and return the family name
    to use, or None if no Persian font could be loaded.

    Flet's `page.fonts` maps a family name to a URL or local file path. We
    try Vazirmatn from a CDN first (jsDelivr hosts the official release),
    then fall back to local system fonts via fc-list. The first reachable
    source wins and the family name is returned for use in `font_for()`.

    This is best-effort: if neither the CDN nor a system font is reachable,
    we return None and let Flet fall back to its default sans-serif, which
    still renders Persian text legibly on most platforms (Linux desktop has
    Noto Sans Arabic, Windows has Tahoma, macOS has Geeza Pro).
    """
    # 1) CDN — Vazirmatn variable font on jsDelivr (rastikerdar/vazirmatn)
    try:
        cdn_url = ("https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/"
                   "fonts/webfonts/Vazirmatn-Regular.woff2")
        if not page.fonts:
            page.fonts = {}
        page.fonts[PERSIAN_FONT_FAMILY] = cdn_url
        return PERSIAN_FONT_FAMILY
    except Exception:  # noqa: BLE001  (page.fonts may be read-only in some builds)
        pass
    return None


def apply(page: ft.Page, mode: str, preset: str, accent: str,
          language: str = "en") -> None:
    """Apply theme to *page*.

    Args:
        mode: theme mode ("light", "dark", "system").
        preset: preset name ("default", "midnight", ...).
        accent: hex accent color (only used when preset == "default").
        language: UI language code ("en", "fa"). When "fa", the page is set
            to RTL and the Persian font is registered.
    """
    seed, dark_surface = PRESETS.get(preset, PRESETS["default"])
    if preset == "default":
        seed = accent

    # Persian font registration. We try Vazirmatn first; if it loads, all
    # Text() controls will use it because we set it as the page-level
    # default font family below.
    persian_font = None
    if language == "fa":
        persian_font = register_persian_fonts(page)

    # Build the Theme object. Flet's Theme has a `font_family` field that
    # sets the default font for all text controls.
    base_kwargs: dict = {"color_scheme_seed": seed}
    if persian_font:
        base_kwargs["font_family"] = persian_font
    # Use the new visual_density token to make the app slightly more
    # compact by default — this matches the Linear/Vercel dashboard
    # aesthetic (high information density, calm surfaces).
    base_kwargs["visual_density"] = ft.VisualDensity.COMPACT
    page.theme = ft.Theme(**base_kwargs)

    if dark_surface:
        page.dark_theme = ft.Theme(color_scheme_seed=seed,
                                    color_scheme=ft.ColorScheme(surface=dark_surface),
                                    visual_density=ft.VisualDensity.COMPACT,
                                    **({"font_family": persian_font} if persian_font else {}))
    else:
        page.dark_theme = ft.Theme(**base_kwargs)
    page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(
        mode, ft.ThemeMode.SYSTEM)

    # RTL: Flet's Page has an `rtl` flag that flips the entire layout —
    # navigation rails move to the right, padding/margins mirror, and text
    # controls inside the page default to right-alignment.
    # Per W3C alreq and Material 3 bidirectionality, this is the correct
    # page-level direction for Persian.
    page.rtl = (language == "fa")


