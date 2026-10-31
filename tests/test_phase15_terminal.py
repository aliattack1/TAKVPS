"""Phase 15 terminal overhaul tests: function keys, direct keyboard capture."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.terminal import key_sequence, ctrl_char


class TestFunctionKeys(unittest.TestCase):
    """Phase 15: F1-F12 sequences are now supported."""
    def test_f1_through_f4(self):
        self.assertEqual(key_sequence("f1"), "\x1bOP")
        self.assertEqual(key_sequence("f2"), "\x1bOQ")
        self.assertEqual(key_sequence("f3"), "\x1bOR")
        self.assertEqual(key_sequence("f4"), "\x1bOS")

    def test_f5_through_f8(self):
        self.assertEqual(key_sequence("f5"), "\x1b[15~")
        self.assertEqual(key_sequence("f6"), "\x1b[17~")
        self.assertEqual(key_sequence("f7"), "\x1b[18~")
        self.assertEqual(key_sequence("f8"), "\x1b[19~")

    def test_f9_through_f12(self):
        self.assertEqual(key_sequence("f9"), "\x1b[20~")
        self.assertEqual(key_sequence("f10"), "\x1b[21~")
        self.assertEqual(key_sequence("f11"), "\x1b[23~")
        self.assertEqual(key_sequence("f12"), "\x1b[24~")


class TestInsertKey(unittest.TestCase):
    """Phase 15: Insert key is now supported."""
    def test_insert(self):
        self.assertEqual(key_sequence("insert"), "\x1b[2~")


class TestExistingKeysStillWork(unittest.TestCase):
    """Make sure adding new keys didn't break the existing ones."""
    def test_arrows(self):
        self.assertEqual(key_sequence("up"), "\x1b[A")
        self.assertEqual(key_sequence("down"), "\x1b[B")
        self.assertEqual(key_sequence("right"), "\x1b[C")
        self.assertEqual(key_sequence("left"), "\x1b[D")

    def test_arrows_app_cursor(self):
        self.assertEqual(key_sequence("up", True), "\x1bOA")
        self.assertEqual(key_sequence("down", True), "\x1bOB")

    def test_special(self):
        self.assertEqual(key_sequence("esc"), "\x1b")
        self.assertEqual(key_sequence("tab"), "\t")
        self.assertEqual(key_sequence("enter"), "\r")
        self.assertEqual(key_sequence("backspace"), "\x7f")
        self.assertEqual(key_sequence("del"), "\x1b[3~")
        self.assertEqual(key_sequence("home"), "\x1b[H")
        self.assertEqual(key_sequence("end"), "\x1b[F")
        self.assertEqual(key_sequence("pgup"), "\x1b[5~")
        self.assertEqual(key_sequence("pgdn"), "\x1b[6~")
        self.assertEqual(key_sequence("shift_tab"), "\x1b[Z")


class TestCtrlChar(unittest.TestCase):
    def test_ctrl_a_through_z(self):
        self.assertEqual(ctrl_char("a"), "\x01")
        self.assertEqual(ctrl_char("c"), "\x03")
        self.assertEqual(ctrl_char("d"), "\x04")
        self.assertEqual(ctrl_char("z"), "\x1a")

    def test_ctrl_brackets(self):
        self.assertEqual(ctrl_char("["), "\x1b")
        self.assertEqual(ctrl_char("\\"), "\x1c")
        self.assertEqual(ctrl_char("]"), "\x1d")

    def test_non_alpha_returns_none(self):
        self.assertIsNone(ctrl_char("1"))
        self.assertIsNone(ctrl_char("!"))
        self.assertIsNone(ctrl_char(""))


if __name__ == "__main__":
    unittest.main()
