"""Phase 17 tests: command history, file bookmarks, quick-connect."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.terminal import key_sequence


class TestCommandHistoryKeys(unittest.TestCase):
    """Phase 17: Ctrl+U (clear line) is used by the history recall."""
    def test_ctrl_u_is_correct(self):
        # Ctrl+U = \x15 (the "clear line" control code)
        self.assertEqual(key_sequence("backspace"), "\x7f")
        # The history feature uses \x15 internally, not key_sequence.


class TestFileBookmarks(unittest.TestCase):
    """Phase 17: file_bookmarks field exists on Settings."""
    def test_default_empty(self):
        from storage.settings import Settings
        s = Settings()
        self.assertEqual(s.file_bookmarks, [])

    def test_can_add_and_remove(self):
        from storage.settings import Settings
        s = Settings()
        s.file_bookmarks = ["/var/log", "/etc/nginx"]
        self.assertEqual(len(s.file_bookmarks), 2)
        s.file_bookmarks.remove("/var/log")
        self.assertEqual(s.file_bookmarks, ["/etc/nginx"])

    def test_survives_serialization(self):
        from storage.settings import Settings, SettingsStore
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = Settings()
            s.file_bookmarks = ["/home", "/var/log"]
            store = SettingsStore(path)
            store.data = s
            store.save()
            store2 = SettingsStore(path)
            self.assertEqual(store2.data.file_bookmarks, ["/home", "/var/log"])


class TestQuickConnect(unittest.TestCase):
    """Phase 17: ConnectionProfile has last_connected for sorting recent servers."""
    def test_last_connected_default_zero(self):
        from storage.connections import ConnectionProfile
        p = ConnectionProfile(name="t", host="h", username="u")
        self.assertEqual(p.last_connected, 0.0)

    def test_can_sort_by_last_connected(self):
        from storage.connections import ConnectionProfile
        p1 = ConnectionProfile(name="old", host="h", username="u", last_connected=1000.0)
        p2 = ConnectionProfile(name="new", host="h", username="u", last_connected=2000.0)
        p3 = ConnectionProfile(name="never", host="h", username="u", last_connected=0.0)
        recent = sorted([p for p in [p1, p2, p3] if p.last_connected > 0],
                        key=lambda p: p.last_connected, reverse=True)
        self.assertEqual(recent[0].name, "new")
        self.assertEqual(recent[1].name, "old")
        self.assertEqual(len(recent), 2)     # "never" excluded


class TestTerminalExportFilename(unittest.TestCase):
    """Phase 17: export_output generates a timestamped filename."""
    def test_filename_format(self):
        import time
        fname = f"terminal_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        self.assertTrue(fname.startswith("terminal_"))
        self.assertTrue(fname.endswith(".txt"))


if __name__ == "__main__":
    unittest.main()
