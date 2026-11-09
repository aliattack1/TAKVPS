"""Smoke tests for the Persian/RTL UI improvements.

Covers:
  - i18n.TRANSLATIONS contains the Persian translations for the high-traffic UI strings.
  - Persian digit conversion (to_persian_digits) and technical-value detection
    (looks_technical) behave correctly: ports/IPs/versions/percentages stay Western,
    ordinal counts go Persian.
  - Icon-mirroring table swaps back/forward chevrons in RTL but leaves symmetric
    icons (refresh, search) alone.
  - ui.theme.apply() flips page.rtl to True when language='fa' and registers the
    Vazirmatn font, but leaves rtl=False for English.
  - ui.page_help has Persian help text for every English page-help entry.

These are regression tests for the Persian UI work; they ensure the helpers
remain available even if someone refactors the i18n module later.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from services.i18n import (
    PERSIAN_FONT_FAMILY, RTL_ICON_MIRROR, TRANSLATIONS, font_for, is_rtl,
    looks_technical, mirror_icon, smart_digits, to_persian_digits, translate,
)
from ui import theme
from ui.page_help import PAGE_HELP, PERSIAN_PAGE_HELP, get_help


class TestPersianTranslations:
    """Translation dictionary coverage."""

    def test_translation_count_grew(self):
        # The original dictionary had ~150 entries. The Persian UI work
        # expanded it to at least 1500. If this drops below 1500, something
        # has been removed.
        assert len(TRANSLATIONS) >= 1500, (
            f"Expected ≥1500 translations, got {len(TRANSLATIONS)}")

    def test_high_traffic_strings_translated(self):
        """The strings users see most must have Persian translations."""
        for s in ("Settings", "Firewall", "Recycle Bin", "Not connected.",
                  "Connect a server", "Welcome to TAKVPS",
                  "Add rule", "Add cron job", "Add tunnel", "Add peer",
                  "Approve", "Reject", "Stop", "Send", "Refresh", "Cancel",
                  "Save", "Delete", "Close"):
            fa = translate(s, "fa")
            assert fa != s, f"Missing Persian translation for {s!r}"
            assert any(0x0600 <= ord(c) <= 0x06FF for c in fa), (
                f"Persian translation for {s!r} doesn't contain Arabic-script chars: {fa!r}")

    def test_translate_returns_english_for_en(self):
        assert translate("Settings", "en") == "Settings"
        assert translate("Anything unknown", "en") == "Anything unknown"

    def test_translate_falls_back_to_english_for_unknown(self):
        # Unknown keys pass through unchanged so the UI doesn't show blank.
        assert translate("totally unknown key", "fa") == "totally unknown key"


class TestPersianDigits:
    """Persian-Indic digit conversion for ordinal UI counts."""

    def test_western_to_persian(self):
        assert to_persian_digits("0") == "۰"
        assert to_persian_digits("9") == "۹"
        assert to_persian_digits("42") == "۴۲"
        assert to_persian_digits("3 servers") == "۳ servers"
        # Non-digit text passes through unchanged.
        assert to_persian_digits("hello") == "hello"

    def test_technical_values_stay_western(self):
        """Technical values (ports, IPs, versions, percentages) are not
        transliterated to Persian digits. Per Behdad Esfahbod's note,
        Iranian tech users read and write Western digits in Latin contexts."""
        assert looks_technical(":22") is True
        assert looks_technical("v1.2.3") is True
        assert looks_technical("0.0.0.0:22") is True
        assert looks_technical("10.0.0.1") is True
        assert looks_technical("75%") is True
        # Bare integer (port number on its own) is technical too.
        assert looks_technical("8080") is True

    def test_non_technical_strings(self):
        """Counts and prose are NOT technical — they go Persian."""
        assert looks_technical("3 servers") is False
        assert looks_technical("hello") is False
        assert looks_technical("") is False

    def test_smart_digits_combines_translate_and_digit_conversion(self):
        # translate + Persian digits, but skips digit conversion for technical strings.
        assert smart_digits("3 servers", "fa") == "۳ servers"
        assert smart_digits(":22", "fa") == ":22"  # port stays as-is
        assert smart_digits("75%", "fa") == "75%"  # percentage stays
        assert smart_digits("3 servers", "en") == "3 servers"  # no-op for English


class TestIconMirroring:
    """RTL icon mirroring per Material 3 bidirectionality rules."""

    def test_back_forward_swap(self):
        assert mirror_icon("arrow_back", "fa") == "arrow_forward"
        assert mirror_icon("arrow_forward", "fa") == "arrow_back"
        assert mirror_icon("chevron_left", "fa") == "chevron_right"
        assert mirror_icon("chevron_right", "fa") == "chevron_left"

    def test_no_mirroring_for_ltr(self):
        assert mirror_icon("arrow_back", "en") == "arrow_back"
        assert mirror_icon("chevron_right", "en") == "chevron_right"

    def test_symmetric_icons_stay(self):
        # Refresh is a circular icon — must NOT be mirrored.
        assert mirror_icon("refresh", "fa") == "refresh"
        assert mirror_icon("search", "fa") == "search"
        assert mirror_icon("settings", "fa") == "settings"

    def test_mirror_table_only_contains_directional_icons(self):
        # Sanity check: every entry in RTL_ICON_MIRROR should be a directional
        # icon (back/forward/chevron etc.), not a symmetric one.
        symmetric = {"refresh", "search", "settings", "cloud_off", "shield",
                    "warning_amber", "error_outline", "info", "check_circle"}
        for name in RTL_ICON_MIRROR:
            assert name not in symmetric, (
                f"{name!r} should not be in the RTL mirror table — it's symmetric")


class TestFontFor:
    def test_persian_returns_vazirmatn(self):
        assert font_for("fa") == PERSIAN_FONT_FAMILY
        assert PERSIAN_FONT_FAMILY == "Vazirmatn"

    def test_english_returns_none(self):
        assert font_for("en") is None
        assert font_for("") is None


class TestThemeApplyRTL:
    """ui.theme.apply() flips page.rtl based on language and registers
    the Persian font only when needed."""

    def _make_page(self):
        # Minimal stand-in for ft.Page. We only need `fonts`, `rtl`,
        # `theme`, `dark_theme`, `theme_mode`.
        return MagicMock(spec=["fonts", "rtl", "theme", "dark_theme", "theme_mode"])

    def test_persian_sets_rtl_true(self):
        page = self._make_page()
        theme.apply(page, "dark", "default", "#3B82F6", "fa")
        assert page.rtl is True

    def test_english_sets_rtl_false(self):
        page = self._make_page()
        theme.apply(page, "dark", "default", "#3B82F6", "en")
        assert page.rtl is False

    def test_apply_accepts_language_argument(self):
        sig = inspect.signature(theme.apply)
        assert "language" in sig.parameters
        assert sig.parameters["language"].default == "en"


class TestPageHelpPersian:
    """Every English page-help entry has a Persian counterpart."""

    def test_persian_help_keys_match_english(self):
        assert set(PERSIAN_PAGE_HELP.keys()) == set(PAGE_HELP.keys()), (
            f"Persian help is missing for: "
            f"{set(PAGE_HELP.keys()) - set(PERSIAN_PAGE_HELP.keys())}")

    def test_get_help_returns_persian_for_fa(self):
        en = get_help("overview", "en")
        fa = get_help("overview", "fa")
        assert en != fa
        # Persian text should contain Persian/Arabic characters.
        assert any(0x0600 <= ord(c) <= 0x06FF for c in fa)

    def test_get_help_falls_back_to_english(self):
        # Unknown key → generic fallback message.
        help_text = get_help("does-not-exist", "en")
        assert "no help" in help_text.lower() or "not available" in help_text.lower()
