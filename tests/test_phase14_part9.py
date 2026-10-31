"""Phase 14 Part 9 tests: mouse reporting in the terminal.

Tests the pure `services/mouse.py` helper (SGR encoding, mode detection,
event parsing) and the `Screen` emulator's mouse-mode flags. No Flet imports.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.mouse import (BUTTON_LEFT, BUTTON_MIDDLE, BUTTON_RIGHT, MOD_ALT, MOD_CTRL,
                              MOD_SHIFT, MODE_MOUSE_ANY, MODE_MOUSE_BUTTON, MODE_MOUSE_SGR,
                              MODE_MOUSE_URXVT, MouseEvent, WHEEL_DOWN, WHEEL_UP,
                              encode_click, encode_wheel, flet_button_to_code, is_mouse_mode,
                              parse_sgr_response, should_report)
from services.terminal import Screen


# ======================================================================
# Phase 14 Part 9 — MouseEvent.encode_sgr
# ======================================================================
class TestEncodeSGR(unittest.TestCase):
    def test_left_click(self):
        m = MouseEvent(BUTTON_LEFT, 10, 5)
        self.assertEqual(m.encode_sgr(), "\x1b[<0;10;5M")

    def test_left_release(self):
        m = MouseEvent(BUTTON_LEFT, 10, 5, release=True)
        self.assertEqual(m.encode_sgr(), "\x1b[<0;10;5m")

    def test_middle_click(self):
        m = MouseEvent(BUTTON_MIDDLE, 10, 5)
        self.assertEqual(m.encode_sgr(), "\x1b[<1;10;5M")

    def test_right_click(self):
        m = MouseEvent(BUTTON_RIGHT, 10, 5)
        self.assertEqual(m.encode_sgr(), "\x1b[<2;10;5M")

    def test_wheel_up(self):
        m = MouseEvent(WHEEL_UP, 10, 5)
        self.assertEqual(m.encode_sgr(), "\x1b[<64;10;5M")

    def test_wheel_down(self):
        m = MouseEvent(WHEEL_DOWN, 10, 5)
        self.assertEqual(m.encode_sgr(), "\x1b[<65;10;5M")

    def test_shift_modifier(self):
        m = MouseEvent(BUTTON_LEFT, 10, 5, shift=True)
        # code = 0 + 4 = 4
        self.assertEqual(m.encode_sgr(), "\x1b[<4;10;5M")

    def test_alt_modifier(self):
        m = MouseEvent(BUTTON_LEFT, 10, 5, alt=True)
        # code = 0 + 8 = 8
        self.assertEqual(m.encode_sgr(), "\x1b[<8;10;5M")

    def test_ctrl_modifier(self):
        m = MouseEvent(BUTTON_LEFT, 10, 5, ctrl=True)
        # code = 0 + 16 = 16
        self.assertEqual(m.encode_sgr(), "\x1b[<16;10;5M")

    def test_all_modifiers(self):
        m = MouseEvent(BUTTON_LEFT, 10, 5, shift=True, alt=True, ctrl=True)
        # code = 0 + 4 + 8 + 16 = 28
        self.assertEqual(m.encode_sgr(), "\x1b[<28;10;5M")

    def test_wheel_with_modifiers(self):
        m = MouseEvent(WHEEL_UP, 10, 5, ctrl=True)
        # code = 64 + 16 = 80
        self.assertEqual(m.encode_sgr(), "\x1b[<80;10;5M")

    def test_code_method(self):
        m = MouseEvent(BUTTON_LEFT, 1, 1, shift=True, ctrl=True)
        self.assertEqual(m.code(), 0 + MOD_SHIFT + MOD_CTRL)

    def test_coordinates_clamped_to_224(self):
        """Legacy safety: SGR coordinates are clamped to 1–224 (some apps
        still expect this range)."""
        m = MouseEvent(BUTTON_LEFT, 300, -5)
        s = m.encode_sgr()
        # x clamped to 224, y clamped to 1
        self.assertIn("224;1", s)

    def test_coordinates_clamped_minimum_1(self):
        m = MouseEvent(BUTTON_LEFT, 0, 0)
        s = m.encode_sgr()
        self.assertIn("1;1", s)


# ======================================================================
# Phase 14 Part 9 — convenience functions
# ======================================================================
class TestEncodeClick(unittest.TestCase):
    def test_basic_click(self):
        s = encode_click(BUTTON_LEFT, 10, 5)
        self.assertEqual(s, "\x1b[<0;10;5M")

    def test_release(self):
        s = encode_click(BUTTON_LEFT, 10, 5, release=True)
        self.assertEqual(s, "\x1b[<0;10;5m")

    def test_with_modifiers(self):
        s = encode_click(BUTTON_LEFT, 10, 5, shift=True, ctrl=True)
        self.assertEqual(s, "\x1b[<20;10;5M")     # 0 + 4 + 16 = 20


class TestEncodeWheel(unittest.TestCase):
    def test_wheel_up(self):
        s = encode_wheel("up", 10, 5)
        self.assertEqual(s, "\x1b[<64;10;5M")

    def test_wheel_down(self):
        s = encode_wheel("down", 10, 5)
        self.assertEqual(s, "\x1b[<65;10;5M")

    def test_with_ctrl(self):
        s = encode_wheel("up", 10, 5, ctrl=True)
        self.assertEqual(s, "\x1b[<80;10;5M")     # 64 + 16 = 80


# ======================================================================
# Phase 14 Part 9 — mode detection
# ======================================================================
class TestIsMouseMode(unittest.TestCase):
    def test_known_mouse_modes(self):
        for m in [MODE_MOUSE_BUTTON, MODE_MOUSE_ANY, MODE_MOUSE_SGR, MODE_MOUSE_URXVT]:
            self.assertTrue(is_mouse_mode(m), f"{m} should be a mouse mode")

    def test_non_mouse_modes(self):
        for m in [1, 25, 47, 1047, 1049, 2004, 0]:
            self.assertFalse(is_mouse_mode(m), f"{m} should not be a mouse mode")


class TestShouldReport(unittest.TestCase):
    def test_button_and_sgr(self):
        self.assertTrue(should_report(True, False, True))

    def test_any_and_sgr(self):
        self.assertTrue(should_report(False, True, True))

    def test_button_without_sgr(self):
        """We don't emit legacy-format mouse events — SGR is required."""
        self.assertFalse(should_report(True, False, False))

    def test_any_without_sgr(self):
        self.assertFalse(should_report(False, True, False))

    def test_sgr_without_reporting_mode(self):
        """SGR alone (without 1000 or 1003) doesn't enable reporting."""
        self.assertFalse(should_report(False, False, True))

    def test_nothing_enabled(self):
        self.assertFalse(should_report(False, False, False))


# ======================================================================
# Phase 14 Part 9 — parse_sgr_response
# ======================================================================
class TestParseSGRResponse(unittest.TestCase):
    def test_parse_click(self):
        m = parse_sgr_response("\x1b[<0;10;5M")
        self.assertIsNotNone(m)
        self.assertEqual(m.button, BUTTON_LEFT)
        self.assertEqual(m.x, 10)
        self.assertEqual(m.y, 5)
        self.assertFalse(m.release)

    def test_parse_release(self):
        m = parse_sgr_response("\x1b[<0;10;5m")
        self.assertIsNotNone(m)
        self.assertTrue(m.release)

    def test_parse_wheel(self):
        m = parse_sgr_response("\x1b[<64;10;5M")
        self.assertIsNotNone(m)
        self.assertEqual(m.button, WHEEL_UP)

    def test_parse_with_modifiers(self):
        m = parse_sgr_response("\x1b[<28;10;5M")     # 0 + 4 + 8 + 16
        self.assertIsNotNone(m)
        self.assertTrue(m.shift)
        self.assertTrue(m.alt)
        self.assertTrue(m.ctrl)
        self.assertEqual(m.button, BUTTON_LEFT)

    def test_parse_invalid_returns_none(self):
        self.assertIsNone(parse_sgr_response(""))
        self.assertIsNone(parse_sgr_response("not a sequence"))
        self.assertIsNone(parse_sgr_response("\x1b[<"))     # no body
        self.assertIsNone(parse_sgr_response("\x1b[<abc;1;2M"))     # non-numeric
        self.assertIsNone(parse_sgr_response("\x1b[<0;10;5X"))     # wrong suffix


# ======================================================================
# Phase 14 Part 9 — flet_button_to_code
# ======================================================================
class TestFletButtonToCode(unittest.TestCase):
    def test_left(self):
        self.assertEqual(flet_button_to_code("left"), BUTTON_LEFT)

    def test_middle(self):
        self.assertEqual(flet_button_to_code("middle"), BUTTON_MIDDLE)

    def test_right(self):
        self.assertEqual(flet_button_to_code("right"), BUTTON_RIGHT)

    def test_unknown_defaults_to_left(self):
        self.assertEqual(flet_button_to_code("???"), BUTTON_LEFT)

    def test_case_insensitive(self):
        self.assertEqual(flet_button_to_code("LEFT"), BUTTON_LEFT)
        self.assertEqual(flet_button_to_code("Right"), BUTTON_RIGHT)

    def test_none_defaults_to_left(self):
        self.assertEqual(flet_button_to_code(None), BUTTON_LEFT)


# ======================================================================
# Phase 14 Part 9 — Screen mouse-mode flags
# ======================================================================
class TestScreenMouseModes(unittest.TestCase):
    def test_defaults_off(self):
        s = Screen(80, 24)
        self.assertFalse(s.mouse_button)
        self.assertFalse(s.mouse_any)
        self.assertFalse(s.mouse_sgr)
        self.assertFalse(s.mouse_reporting)

    def test_enable_button_mode(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1000h")     # DECSET 1000
        self.assertTrue(s.mouse_button)
        self.assertFalse(s.mouse_reporting)     # SGR not yet on

    def test_enable_sgr(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1006h")     # DECSET 1006
        self.assertTrue(s.mouse_sgr)
        self.assertFalse(s.mouse_reporting)     # no reporting mode yet

    def test_enable_button_and_sgr(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1006h")
        self.assertTrue(s.mouse_button)
        self.assertTrue(s.mouse_sgr)
        self.assertTrue(s.mouse_reporting)

    def test_enable_any_and_sgr(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1003h\x1b[?1006h")
        self.assertTrue(s.mouse_any)
        self.assertTrue(s.mouse_sgr)
        self.assertTrue(s.mouse_reporting)

    def test_disable_button_mode(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1006h")
        self.assertTrue(s.mouse_reporting)
        s.feed("\x1b[?1000l")     # DECRST 1000
        self.assertFalse(s.mouse_button)
        self.assertFalse(s.mouse_reporting)

    def test_disable_sgr(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1006h")
        s.feed("\x1b[?1006l")     # DECRST 1006
        self.assertFalse(s.mouse_sgr)
        self.assertFalse(s.mouse_reporting)

    def test_reset_clears_mouse_flags(self):
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1006h")
        s.feed("\x1bc")     # RIS — full reset
        self.assertFalse(s.mouse_button)
        self.assertFalse(s.mouse_any)
        self.assertFalse(s.mouse_sgr)
        self.assertFalse(s.mouse_reporting)

    def test_urxvt_mode_accepted_but_no_reporting(self):
        """Mode 1015 (urxvt) is accepted but doesn't enable reporting on its own."""
        s = Screen(80, 24)
        s.feed("\x1b[?1015h")
        # urxvt alone doesn't set our flags; we always emit SGR.
        self.assertFalse(s.mouse_reporting)


# ======================================================================
# Phase 14 Part 9 — realistic scenarios
# ======================================================================
class TestRealisticScenarios(unittest.TestCase):
    def test_vim_mouse_click_sequence(self):
        """vim enables mouse reporting on startup; a click sends the SGR sequence."""
        # vim sends: DECSET 1000, DECSET 1003, DECSET 1006
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1003h\x1b[?1006h")
        self.assertTrue(s.mouse_reporting)
        # A click at col 30, row 10
        seq = encode_click(BUTTON_LEFT, 30, 10)
        self.assertEqual(seq, "\x1b[<0;30;10M")
        # And the release
        rel = encode_click(BUTTON_LEFT, 30, 10, release=True)
        self.assertEqual(rel, "\x1b[<0;30;10m")

    def test_less_scroll_sequence(self):
        """less uses mouse wheel for scrolling."""
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1006h")
        # Wheel up at col 40, row 12
        seq = encode_wheel("up", 40, 12)
        self.assertEqual(seq, "\x1b[<64;40;12M")

    def test_tmux_click_with_modifier(self):
        """tmux supports shift+click to extend selection."""
        s = Screen(80, 24)
        s.feed("\x1b[?1000h\x1b[?1006h")
        seq = encode_click(BUTTON_LEFT, 20, 5, shift=True)
        self.assertEqual(seq, "\x1b[<4;20;5M")     # 0 + 4 = 4


if __name__ == "__main__":
    unittest.main()
