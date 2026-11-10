import unittest

from services.editor import (decode_bytes, encode_text, find_all, language_for, line_col,
                             replace_all, tokenize, tokenize_lines)


class EditorTests(unittest.TestCase):
    def test_encoding_roundtrip(self):
        t, enc, eol = decode_bytes(b"a\r\nb\r\n")
        self.assertEqual((t, eol), ("a\nb\n", "\r\n"))
        self.assertEqual(encode_text(t, enc, eol), b"a\r\nb\r\n")
        self.assertEqual(decode_bytes(b"h\xe9llo")[1], "latin-1")
        self.assertEqual(decode_bytes(b"\xef\xbb\xbfhi")[1], "utf-8-sig")

    def test_language(self):
        self.assertEqual(language_for("/etc/nginx/nginx.conf"), "conf")
        self.assertEqual(language_for("app.PY"), "python")
        self.assertEqual(language_for("x.unknown"), "text")

    def test_tokenize_covers_text(self):
        src = 'def f(x):\n    # hi\n    return "a#b" + 12  /* no */\n'
        toks = tokenize(src, "python")
        self.assertEqual("".join(c for c, _ in toks), src)
        kinds = {c: k for c, k in toks}
        self.assertEqual(kinds["def"], "keyword")
        self.assertEqual(kinds["# hi"], "comment")
        self.assertEqual(kinds['"a#b"'], "string")
        self.assertEqual(kinds["12"], "number")

    def test_block_comment_splits_lines(self):
        lines = tokenize_lines("a /* x\ny */ b", "javascript")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1][0], ("y */", "comment"))

    def test_find_replace(self):
        self.assertEqual(find_all("Foo foo", "foo"), [(0, 3), (4, 7)])
        self.assertEqual(find_all("Foo foo", "foo", case=True), [(4, 7)])
        self.assertEqual(replace_all("a1 b22", r"\d+", "#", regex=True), ("a# b#", 2))
        self.assertEqual(replace_all("a.b", ".", "-"), ("a-b", 1))     # literal, not regex
        with self.assertRaises(ValueError):
            find_all("x", "(", regex=True)

    def test_line_col(self):
        self.assertEqual(line_col("ab\ncd", 4), (2, 2))


if __name__ == "__main__":
    unittest.main()
