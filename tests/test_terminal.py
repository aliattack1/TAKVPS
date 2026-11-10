import unittest

from services.terminal import Screen, ctrl_char, key_sequence


def lines(s):
    return ["".join(c for c, _ in row).rstrip() for row in s.grid]


class ScreenTests(unittest.TestCase):
    def test_text_cr_lf_and_wrap(self):
        s = Screen(10, 4)
        s.feed("hello\r\nworld")
        self.assertEqual(lines(s)[:2], ["hello", "world"])
        s = Screen(5, 3)
        s.feed("abcdefg")
        self.assertEqual(lines(s)[:2], ["abcde", "fg"])

    def test_cursor_and_erase(self):
        s = Screen(10, 4)
        s.feed("abcdef\x1b[3D\x1b[K")                 # back 3, erase to end of line
        self.assertEqual(lines(s)[0], "abc")
        s.feed("\x1b[2;3Hxy")
        self.assertEqual(lines(s)[1], "  xy")
        s.feed("\x1b[2J\x1b[H")
        self.assertEqual(lines(s), ["", "", "", ""])

    def test_sgr_colors(self):
        s = Screen(10, 2)
        s.feed("\x1b[1;31mA\x1b[0mB\x1b[38;5;200mC\x1b[38;2;1;2;3mD")
        st = [c[1] for c in s.grid[0][:4]]
        self.assertEqual(st[0], (1, None, 1))
        self.assertEqual(st[1], (None, None, 0))
        self.assertEqual(st[2][0], 200)
        self.assertEqual(st[3][0], "#010203")

    def test_scrollback_and_scroll_region(self):
        s = Screen(10, 3)
        s.feed("1\r\n2\r\n3\r\n4")
        self.assertEqual(s.scrolled_total, 1)
        self.assertEqual("".join(c for c, _ in s.scrollback[0]).strip(), "1")
        self.assertEqual(lines(s), ["2", "3", "4"])
        s = Screen(10, 4)
        s.feed("a\r\nb\r\nc\r\nd\x1b[2;3r\x1b[3;1H\n")   # region rows 2-3, LF at bottom of region
        self.assertEqual(lines(s)[0], "a")
        self.assertEqual(lines(s)[3], "d")
        self.assertEqual(s.scrolled_total, 0)

    def test_alt_screen_roundtrip(self):
        s = Screen(10, 3)
        s.feed("main")
        s.feed("\x1b[?1049h\x1b[Halt")
        self.assertTrue(s.alt_active)
        self.assertEqual(lines(s)[0], "alt")
        s.feed("\x1b[?1049l")
        self.assertEqual(lines(s)[0], "main")

    def test_insert_delete_and_responses(self):
        s = Screen(10, 3)
        s.feed("a\r\nb\r\nc\x1b[2;1H\x1b[L")
        self.assertEqual(lines(s), ["a", "", "b"])
        s.feed("\x1b[M")
        self.assertEqual(lines(s), ["a", "b", ""])
        s.feed("\x1b[6n")
        self.assertEqual(s.take_responses(), "\x1b[2;1R")
        s.feed("\x1b]0;my title\x07")
        self.assertEqual(s.title, "my title")

    def test_app_cursor_mode(self):
        s = Screen(10, 4)
        s.feed("\x1b[?1h")
        self.assertTrue(s.app_cursor)
        self.assertEqual(key_sequence("up", True), "\x1bOA")
        self.assertEqual(key_sequence("up", False), "\x1b[A")

    def test_resize_shrink_and_grow(self):
        s = Screen(10, 6)
        s.feed("1\r\n2\r\n3\r\n4\r\n5\r\n6")
        s.resize(10, 4)                                # cursor is at the bottom: top rows -> scrollback
        self.assertEqual(lines(s), ["3", "4", "5", "6"])
        self.assertEqual(s.scrolled_total, 2)
        s.resize(14, 6)
        self.assertEqual((len(s.grid), len(s.grid[0])), (6, 14))

    def test_ctrl(self):
        self.assertEqual(ctrl_char("c"), "\x03")
        self.assertEqual(ctrl_char("["), "\x1b")
        self.assertIsNone(ctrl_char("1"))


if __name__ == "__main__":
    unittest.main()
