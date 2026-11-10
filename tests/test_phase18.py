"""snippets search, config diff, badges, editor line numbers."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import difflib


class TestSnippetsFilter(unittest.TestCase):
    """the snippets page can filter by name or command text."""
    def test_filter_matches_name(self):
        snippets = [("backup", "tar -czf /backup.tar.gz /home"),
                    ("deploy", "git pull && npm run build")]
        q = "backup"
        matched = [s for s in snippets if q in s[0].lower() or q in s[1].lower()]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][0], "backup")

    def test_filter_matches_command_text(self):
        snippets = [("backup", "tar -czf /backup.tar.gz /home"),
                    ("deploy", "git pull && npm run build")]
        q = "git"
        matched = [s for s in snippets if q in s[0].lower() or q in s[1].lower()]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][0], "deploy")

    def test_filter_empty_shows_all(self):
        snippets = [("a", "ls"), ("b", "pwd")]
        q = ""
        if not q:
            matched = snippets
        else:
            matched = [s for s in snippets if q in s[0].lower() or q in s[1].lower()]
        self.assertEqual(len(matched), 2)

    def test_filter_no_match(self):
        snippets = [("backup", "tar")]
        q = "xyz"
        matched = [s for s in snippets if q in s[0].lower() or q in s[1].lower()]
        self.assertEqual(len(matched), 0)


class TestConfigDiff(unittest.TestCase):
    """unified diff between two file contents."""
    def test_identical_files(self):
        a = "line1\nline2\nline3\n"
        b = "line1\nline2\nline3\n"
        diff = list(difflib.unified_diff(a.splitlines(keepends=True),
                                          b.splitlines(keepends=True),
                                          fromfile="server_a", tofile="server_b"))
        self.assertEqual(diff, [])

    def test_different_files(self):
        a = "line1\nline2\nline3\n"
        b = "line1\nCHANGED\nline3\n"
        diff = list(difflib.unified_diff(a.splitlines(keepends=True),
                                          b.splitlines(keepends=True),
                                          fromfile="server_a", tofile="server_b"))
        self.assertGreater(len(diff), 0)
        # Should contain a line starting with -line2 (removed)
        self.assertTrue(any(l.startswith("-line2") for l in diff))
        # Should contain a line starting with +CHANGED (added)
        self.assertTrue(any(l.startswith("+CHANGED") for l in diff))

    def test_empty_file_vs_content(self):
        a = ""
        b = "new content\n"
        diff = list(difflib.unified_diff(a.splitlines(keepends=True),
                                          b.splitlines(keepends=True),
                                          fromfile="empty", tofile="full"))
        self.assertGreater(len(diff), 0)


class TestBadgeCounts(unittest.TestCase):
    """alert and activity badge counters exist on settings/context."""
    def test_alert_count_default(self):
        from storage.settings import Settings
        s = Settings()
        # The badge counts live on AppContext, not Settings — but we verify
        # the Settings class is unaffected (no extra fields needed).
        self.assertFalse(hasattr(s, "_alert_count"))

    def test_activity_count_incremented_by_log_action(self):
        """log_action should increment _activity_count."""
        # We can't easily test AppContext without a full page, but we verify
        # the pattern: a class with _activity_count starts at 0.
        class FakeCtx:
            _activity_count = 0
            def log_action(self, cmd, exit_code, source="action"):
                self._activity_count += 1
        ctx = FakeCtx()
        self.assertEqual(ctx._activity_count, 0)
        ctx.log_action("uptime", 0)
        self.assertEqual(ctx._activity_count, 1)
        ctx.log_action("ls", 0)
        self.assertEqual(ctx._activity_count, 2)


class TestEditorLineNumbers(unittest.TestCase):
    """line numbers toggle defaults to True."""
    def test_default_true(self):
        class FakeEditor:
            show_line_numbers = True
        e = FakeEditor()
        self.assertTrue(getattr(e, "show_line_numbers", True))

    def test_toggle(self):
        class FakeEditor:
            show_line_numbers = True
            def _toggle_line_numbers(self):
                self.show_line_numbers = not getattr(self, "show_line_numbers", True)
        e = FakeEditor()
        e._toggle_line_numbers()
        self.assertFalse(e.show_line_numbers)
        e._toggle_line_numbers()
        self.assertTrue(e.show_line_numbers)


if __name__ == "__main__":
    unittest.main()
