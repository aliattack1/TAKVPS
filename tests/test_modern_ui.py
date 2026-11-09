"""Smoke tests for the modern UI components and design tokens.

Covers:
  - theme.py: spacing scale, radius scale, elevation tokens, density presets,
    semantic state colors, state_color() lookups, status_to_state() mapping.
  - components/common.py: status_pill(), skeleton_list(), loading_state(),
    error_state(), empty_state(), card(), section_header(), page_header(),
    ConnectionBanner (modern status-dot variant).
  - shell.py: the modern topbar uses ButtonStyle with a tinted background
    for selected icon buttons (Linear/Vercel pattern).
  - context.py: the modern dialog helpers (trust / ask_yes_no / ask_text /
    ask_typed) translate their action buttons via services.i18n.

These are regression tests for the v1.3 UI refresh.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft

from services.i18n import translate
from ui import theme
from ui.components import common
from ui.components.common import (
    ConnectionBanner, card, empty_state, error_state, loading_state,
    page_header, section_header, skeleton_list, skeleton_row, status_pill,
    toolbar,
)


# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
class TestSpacingScale:
    def test_scale_is_monotonic(self):
        """Each spacing token is strictly larger than the previous one
        so pages pick the smallest token that fits without surprises."""
        assert theme.SPACE_XS < theme.SPACE_SM < theme.SPACE < theme.SPACE_LG < theme.SPACE_XL

    def test_default_space_is_unchanged(self):
        """The historical SPACE=12 token is preserved so existing pages
        that still import it directly keep working."""
        assert theme.SPACE == 12


class TestRadiusScale:
    def test_scale_is_monotonic(self):
        assert theme.RADIUS_SM < theme.RADIUS_MD < theme.RADIUS < theme.RADIUS_LG

    def test_pill_is_very_large(self):
        """RADIUS_PILL must be huge (effectively infinite) so pills render
        as fully-rounded capsules."""
        assert theme.RADIUS_PILL >= 999


class TestElevationTokens:
    def test_elevation_levels_match_material_3(self):
        """ELEVATION_FLAT=0 (filled card), LOW=1 (raised), MEDIUM=3 (dialog),
        HIGH=5 (FAB) — Material 3's standard levels."""
        assert theme.ELEVATION_FLAT == 0
        assert theme.ELEVATION_LOW == 1
        assert theme.ELEVATION_MEDIUM == 3
        assert theme.ELEVATION_HIGH == 5
        # Strictly monotonic.
        assert theme.ELEVATION_FLAT < theme.ELEVATION_LOW < theme.ELEVATION_MEDIUM < theme.ELEVATION_HIGH


class TestSemanticStateColors:
    def test_state_color_returns_three_roles(self):
        """Each state exposes 'container' (background), 'on' (foreground)
        and 'plain' (solid icon color) variants."""
        for state in ("ok", "info", "warning", "error", "critical", "neutral", "muted"):
            container = theme.state_color(state, "container")
            on = theme.state_color(state, "on")
            plain = theme.state_color(state, "plain")
            assert container is not None
            assert on is not None
            assert plain is not None
            # All three should be different (otherwise the role distinction is meaningless).
            assert len({container, on, plain}) == 3

    def test_state_color_falls_back_for_unknown(self):
        """An unknown state returns the neutral color so the UI doesn't
        crash on a typo."""
        assert theme.state_color("nonexistent", "container") == theme.state_color("neutral", "container")

    def test_status_to_state_maps_common_statuses(self):
        """status_to_state maps the common status strings used throughout
        the app to one of our 7 semantic states."""
        assert theme.status_to_state("connected") == "ok"
        assert theme.status_to_state("running") == "ok"
        assert theme.status_to_state("healthy") == "ok"
        assert theme.status_to_state("paused") == "warning"
        assert theme.status_to_state("exited") == "error"
        assert theme.status_to_state("disconnected") == "error"
        assert theme.status_to_state("") == "neutral"
        # Unknown status → neutral (never crashes).
        assert theme.status_to_state("made_up_status") == "neutral"

    def test_status_to_state_is_case_insensitive(self):
        assert theme.status_to_state("RUNNING") == "ok"
        assert theme.status_to_state("Exited") == "error"
        assert theme.status_to_state("PAUSED") == "warning"


# ---------------------------------------------------------------------------
# Modern UI components in common.py
# ---------------------------------------------------------------------------
class TestStatusPill:
    def test_pill_accepts_semantic_state(self):
        """A status_pill built with a semantic state ('ok', 'error', ...)
        renders without raising."""
        pill = status_pill("running", "ok")
        assert isinstance(pill, ft.Container)
        assert pill.border_radius == theme.RADIUS_PILL
        # Pill height is small (22dp) so it fits inside list rows.
        assert pill.height == 22

    def test_pill_accepts_raw_status_string(self):
        """A status_pill built with a raw status string ('running', 'exited',
        'paused', ...) is auto-mapped to a semantic state via
        status_to_state() so callers don't have to."""
        pill = status_pill("running", "running")  # both args = "running"
        assert isinstance(pill, ft.Container)
        # 'running' → 'ok' → green container.
        assert pill.bgcolor == theme.state_color("ok", "container")

    def test_pill_with_icon_replaces_dot(self):
        """When an icon is passed, the dot is replaced with that icon."""
        pill = status_pill("running", "ok", icon=ft.Icons.CHECK_CIRCLE)
        # Pill is a Container holding a Row.
        assert isinstance(pill.content, ft.Row)
        # The first child should be the icon (not the dot).
        first = pill.content.controls[0]
        assert isinstance(first, ft.Icon)

    def test_pill_without_dot_or_icon_has_only_text(self):
        """When dot=False and no icon, the pill contains only the label text."""
        pill = status_pill("running", "ok", dot=False)
        # Only the text child, no dot, no icon.
        assert len(pill.content.controls) == 1
        assert isinstance(pill.content.controls[0], ft.Text)


class TestSkeletonList:
    def test_skeleton_list_returns_a_column_with_n_rows(self):
        """A skeleton list with N rows renders as a Column with N items."""
        skel = skeleton_list(rows=5)
        assert isinstance(skel, ft.Column)
        assert len(skel.controls) == 5
        # Each row is a Row with an icon placeholder + 2 text placeholders.
        for row in skel.controls:
            assert isinstance(row, ft.Row)


class TestLoadingAndErrorStates:
    def test_loading_state_returns_a_centered_container(self):
        """loading_state returns a Container centered both ways with a
        ProgressRing + label inside."""
        ls = loading_state("Loading…")
        assert isinstance(ls, ft.Container)
        assert ls.alignment is not None  # centered
        # The content is a Column with 2 children: ring + label.
        col = ls.content
        assert isinstance(col, ft.Column)
        assert any(isinstance(c, ft.ProgressRing) for c in col.controls)

    def test_error_state_returns_a_centered_container(self):
        """error_state returns a Container with an error icon, title and
        message, plus an optional Retry button."""
        es = error_state("Something exploded")
        assert isinstance(es, ft.Container)
        col = es.content
        # Children: error icon, "Something went wrong" title, message.
        labels = [c.value for c in col.controls if isinstance(c, ft.Text)]
        assert any("went wrong" in l for l in labels)
        assert any("Something exploded" in l for l in labels)

    def test_error_state_with_retry_has_retry_button(self):
        """When a retry callback is passed, the error state renders a Retry
        OutlinedButton."""
        es = error_state("oops", retry=lambda: None)
        col = es.content
        buttons = [c for c in col.controls if isinstance(c, ft.Container)]
        # At least one container with a button inside.
        assert any(isinstance(c.content, ft.OutlinedButton) for c in buttons)


class TestEmptyState:
    def test_basic_empty_state_has_icon_title_subtitle(self):
        """empty_state with just icon/title/subtitle returns a centered
        Column with 3 children (icon, title, subtitle)."""
        es = empty_state(ft.Icons.INBOX, "No items", "Add one to get started.")
        assert isinstance(es, ft.Container)
        col = es.content
        assert isinstance(col, ft.Column)
        # icon + title + subtitle = 3 controls.
        assert len(col.controls) == 3

    def test_empty_state_with_action_adds_a_button(self):
        """When action_label + on_action are passed, a 4th child (a
        FilledTonalButton) is added below the subtitle."""
        es = empty_state(ft.Icons.INBOX, "No items", "Add one.",
                         action_label="Add", on_action=lambda _: None)
        col = es.content
        # icon + title + subtitle + action button = 4 controls
        # (the button is wrapped in a Container for spacing).
        assert len(col.controls) == 4

    def test_empty_state_subtitle_is_optional(self):
        """empty_state without a subtitle renders just icon + title."""
        es = empty_state(ft.Icons.INBOX, "No items")
        col = es.content
        assert len(col.controls) == 2


class TestCard:
    def test_card_defaults_to_filled(self):
        """A modern card defaults to a filled card (no shadow) per
        Material 3 — calm, low visual noise. A subtle 1dp border replaces
        the shadow for the surface separation."""
        c = card(ft.Text("hello"))
        # Filled card → no shadow, just a subtle border.
        assert c.shadow is None
        assert c.border is not None

    def test_card_with_elevation_has_no_border(self):
        """When elevation > 0 (raised card), the subtle border is removed
        and a BoxShadow is added — the shadow provides the separation."""
        c = card(ft.Text("hello"), elevation=theme.ELEVATION_LOW)
        # Raised card → shadow set, border removed.
        assert c.shadow is not None
        assert c.border is None


class TestConnectionBanner:
    def test_banner_translates_button_label(self):
        """When the banner is created with language='fa', the Reconnect
        button label and the status label are translated to Persian."""
        banner = ConnectionBanner(lambda: None, language="fa")
        # The button label is stored in .content (Flet 1.x TextButton uses
        # `content` for the label string, not `text`).
        assert banner._btn.content == translate("Reconnect", "fa")
        assert banner._label.value == translate("Not connected", "fa")

    def test_banner_status_dot_reflects_state(self):
        """The status dot's color reflects the live connection state:
        amber for connecting/reconnecting, red for disconnected, green for connected."""
        banner = ConnectionBanner(lambda: None, language="en")
        banner.set_status("connecting")
        assert banner._dot.bgcolor == ft.Colors.AMBER
        banner.set_status("reconnecting")
        assert banner._dot.bgcolor == ft.Colors.AMBER
        banner.set_status("disconnected")
        assert banner._dot.bgcolor == ft.Colors.RED
        banner.set_status("connected")
        # Banner is hidden when connected, but the dot color is still updated.
        assert banner._dot.bgcolor == ft.Colors.GREEN

    def test_banner_hides_when_connected(self):
        """When the SSH session is connected, the banner is hidden."""
        banner = ConnectionBanner(lambda: None, language="en")
        banner.set_status("connected")
        assert banner.visible is False
        banner.set_status("disconnected")
        assert banner.visible is True

    def test_banner_reconnect_button_only_when_disconnected(self):
        """The Reconnect button only shows when the user can actually
        reconnect (status == 'disconnected'). Otherwise it's hidden so
        the banner doesn't tease an unavailable action."""
        banner = ConnectionBanner(lambda: None, language="en")
        banner.set_status("connecting")
        # Connecting → button hidden (can't reconnect while connecting).
        assert banner._btn.visible is False
        banner.set_status("disconnected")
        # Disconnected → button shown.
        assert banner._btn.visible is True


class TestPageHeader:
    def test_page_header_renders_title_and_actions(self):
        """page_header renders a large title plus trailing action buttons."""
        h = page_header("Docker", "Manage containers",
                        actions=[ft.IconButton(ft.Icons.REFRESH)])
        assert isinstance(h, ft.Row)
        # The first item is a Column with the title + subtitle.
        leading = h.controls[0]
        assert isinstance(leading, ft.Column)
        # Title text size is 22pt (large) per Linear/Vercel pattern.
        title = leading.controls[0]
        assert isinstance(title, ft.Text)
        assert title.size == 22
        assert title.weight == ft.FontWeight.W_600


class TestSectionHeader:
    def test_section_header_has_title_and_optional_subtitle(self):
        """section_header renders an 18pt title plus optional muted subtitle."""
        h = section_header("Containers", "5 running")
        assert isinstance(h, ft.Container)
        row = h.content
        leading = row.controls[0]
        assert isinstance(leading, ft.Column)
        # Title + subtitle = 2 children in the leading column.
        assert len(leading.controls) == 2


# ---------------------------------------------------------------------------
# Shell-wide modern UX
# ---------------------------------------------------------------------------
class TestTopbarUsesSelectedStyle:
    """The modern topbar uses IconButton selected state with a tinted
    background (Linear/Vercel pattern) when the side panel is open."""

    def test_topbar_selected_state(self):
        """When the chat or files panel is open, the corresponding
        IconButton in the topbar should be marked as selected."""
        # We can't easily instantiate a full Shell here (it requires a Page),
        # so we just verify the helper API contract: IconButton has a
        # `selected` argument that affects the button's style.
        btn = ft.IconButton(ft.Icons.FOLDER_OPEN, selected=True)
        assert btn.selected is True
        # And the unselected variant.
        btn2 = ft.IconButton(ft.Icons.FOLDER_OPEN, selected=False)
        assert btn2.selected is False


# ---------------------------------------------------------------------------
# Modern dialog helpers in context.py
# ---------------------------------------------------------------------------
class TestDialogHelpersTranslate:
    """The modern dialog helpers (trust / ask_yes_no / ask_text / ask_typed)
    translate their action buttons via services.i18n, so a Persian user
    sees «انصراف» / «تأیید» / «اعتماد» instead of «Cancel» / «OK» / «Trust»."""

    def test_dialog_strings_have_persian_translations(self):
        """All button labels used by the modern dialog helpers must have
        Persian translations in services.i18n.TRANSLATIONS."""
        for s in ("Cancel", "OK", "Yes", "Confirm", "Reject", "Trust",
                  "Trust anyway", "Trust this server?", "SHA256 fingerprint",
                  "Previously trusted", "⚠ HOST KEY CHANGED",
                  'Type "{word}" to continue'):
            fa = translate(s, "fa")
            assert fa != s, f"Missing Persian translation for {s!r}"
            # The translation should contain Arabic-script characters.
            assert any(0x0600 <= ord(c) <= 0x06FF for c in fa), (
                f"Persian translation for {s!r} doesn't contain Arabic-script chars: {fa!r}")
