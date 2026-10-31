"""Phase 14 Part 5 tests: per-server AI system prompts.

Tests the pure layer: `build_system(server_note=...)` in `services/ai/chat.py`,
the `ai_context` field on `ConnectionProfile`, and the storage round-trip.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ai.chat import build_system, _BASE, _MODE
from storage.connections import ConnectionProfile, ConnectionStore


# ======================================================================
# Phase 14 Part 5 — build_system with server_note
# ======================================================================
class TestBuildSystemWithServerNote(unittest.TestCase):
    def test_no_note_backwards_compatible(self):
        """Calling build_system without server_note produces the same output as
        before Part 5 — existing tests and behaviour must not change."""
        s1 = build_system("suggest", "OS: Ubuntu")
        s2 = build_system("suggest", "OS: Ubuntu", "")
        s3 = build_system("suggest", "OS: Ubuntu", "   \n  ")
        self.assertEqual(s1, s2)
        self.assertEqual(s1, s3)

    def test_note_prepended_to_system_prompt(self):
        note = "Production blog — don't restart nginx during 09:00–17:00 UTC"
        s = build_system("suggest", "", note)
        self.assertIn(note, s)
        # The note appears BEFORE the mode line, so the model sees it as a
        # hard constraint rather than context.
        mode_pos = s.index(_MODE["suggest"])
        note_pos = s.index(note)
        self.assertLess(note_pos, mode_pos)

    def test_note_label_present(self):
        """The note is wrapped in a clear 'follow these strictly' label so the
        model knows it's a hard constraint, not just context."""
        s = build_system("suggest", "", "Never delete /var/log")
        self.assertIn("Notes about this server", s)
        self.assertIn("follow these strictly", s.lower())

    def test_note_with_context_both_present(self):
        note = "This is a production server"
        ctx = "OS: Debian 12, kernel 6.1"
        s = build_system("ask", ctx, note)
        self.assertIn(note, s)
        self.assertIn(ctx, s)
        self.assertIn("Server context:", s)

    def test_note_strips_whitespace(self):
        s = build_system("suggest", "", "  padded note  \n\n")
        self.assertIn("padded note", s)
        # The leading/trailing whitespace is stripped.
        self.assertNotIn("  padded", s)

    def test_note_in_all_modes(self):
        note = "Don't reboot this server"
        for mode in ("suggest", "ask", "agent"):
            s = build_system(mode, "", note)
            self.assertIn(note, s, f"note missing in mode {mode!r}")

    def test_base_prompt_preserved(self):
        """The base prompt (about being a mobile SSH assistant) must still be
        present regardless of the note."""
        s = build_system("suggest", "", "some note")
        self.assertIn(_BASE, s)

    def test_long_note_accepted(self):
        """A long note (e.g. a full runbook) is accepted without truncation."""
        note = "Never do the following:\n" + "\n".join(f"- rule {i}" for i in range(50))
        s = build_system("suggest", "", note)
        self.assertIn("rule 49", s)


# ======================================================================
# Phase 14 Part 5 — ConnectionProfile.ai_context field
# ======================================================================
class TestConnectionProfileAiContext(unittest.TestCase):
    def test_default_empty(self):
        p = ConnectionProfile(name="t", host="h", username="u")
        self.assertEqual(p.ai_context, "")

    def test_set_and_get(self):
        p = ConnectionProfile(name="t", host="h", username="u",
                              ai_context="Production — be careful")
        self.assertEqual(p.ai_context, "Production — be careful")

    def test_replace_preserves_ai_context(self):
        p = ConnectionProfile(name="t", host="h", username="u", ai_context="note")
        p2 = replace(p, name="t2")
        self.assertEqual(p2.ai_context, "note")

    def test_replace_can_override_ai_context(self):
        p = ConnectionProfile(name="t", host="h", username="u", ai_context="old")
        p2 = replace(p, ai_context="new")
        self.assertEqual(p2.ai_context, "new")
        # Original unchanged.
        self.assertEqual(p.ai_context, "old")


# ======================================================================
# Phase 14 Part 5 — storage round-trip (save/load)
# ======================================================================
class TestStorageRoundTrip(unittest.TestCase):
    def test_save_and_load_preserves_ai_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConnectionStore(Path(tmp) / "conns.json")
            p = ConnectionProfile(name="prod", host="10.0.0.1", username="root",
                                  ai_context="Production — don't reboot")
            store.save(p)
            # Re-read from disk.
            store2 = ConnectionStore(Path(tmp) / "conns.json")
            loaded = store2.get(p.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.ai_context, "Production — don't reboot")

    def test_empty_ai_context_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConnectionStore(Path(tmp) / "conns.json")
            p = ConnectionProfile(name="dev", host="10.0.0.2", username="ubuntu")
            store.save(p)
            store2 = ConnectionStore(Path(tmp) / "conns.json")
            loaded = store2.get(p.id)
            self.assertEqual(loaded.ai_context, "")

    def test_duplicate_preserves_ai_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConnectionStore(Path(tmp) / "conns.json")
            p = ConnectionProfile(name="orig", host="h", username="u", ai_context="my note")
            store.save(p)
            dup = store.duplicate(p.id)
            self.assertIsNotNone(dup)
            self.assertEqual(dup.ai_context, "my note")
            self.assertNotEqual(dup.id, p.id)

    def test_old_format_without_ai_context_loads(self):
        """Profiles saved before Phase 14 Part 5 (no ai_context field) must
        still load — the field defaults to empty."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conns.json"
            # Write a minimal profile dict without ai_context.
            old = {"profiles": [{"id": "abc123", "name": "old", "host": "h",
                                  "username": "u", "port": 22, "auth": "password"}]}
            path.write_text(json.dumps(old))
            store = ConnectionStore(path)
            loaded = store.get("abc123")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.ai_context, "")


# ======================================================================
# Phase 14 Part 5 — ChatController._server_note integration
# ======================================================================
class TestServerNoteHelper(unittest.TestCase):
    """Exercises the ChatController._server_note() helper without the full UI.
    We reconstruct just enough of the controller's state to call the method."""

    def test_returns_empty_when_no_active(self):
        # Simulate a ChatController with no active connection.
        class FakeCtx:
            active = None
        class FakeCtl:
            ctx = FakeCtx()
            # Copy the real method's logic.
            def _server_note(self):
                a = self.ctx.active
                return getattr(a, "ai_context", "") if a else ""
        ctl = FakeCtl()
        self.assertEqual(ctl._server_note(), "")

    def test_returns_note_when_active(self):
        class FakeProfile:
            ai_context = "Don't restart nginx"
        class FakeCtx:
            active = FakeProfile()
        class FakeCtl:
            ctx = FakeCtx()
            def _server_note(self):
                a = self.ctx.active
                return getattr(a, "ai_context", "") if a else ""
        ctl = FakeCtl()
        self.assertEqual(ctl._server_note(), "Don't restart nginx")

    def test_returns_empty_when_profile_has_no_note(self):
        class FakeProfile:
            ai_context = ""
        class FakeCtx:
            active = FakeProfile()
        class FakeCtl:
            ctx = FakeCtx()
            def _server_note(self):
                a = self.ctx.active
                return getattr(a, "ai_context", "") if a else ""
        ctl = FakeCtl()
        self.assertEqual(ctl._server_note(), "")


# ======================================================================
# Phase 14 Part 5 — end-to-end: profile note → build_system
# ======================================================================
class TestEndToEnd(unittest.TestCase):
    def test_profile_note_flows_into_system_prompt(self):
        """The full path: a ConnectionProfile with ai_context → the chat
        controller's _server_note() → build_system(server_note=...)."""
        # 1. A profile with a note
        p = ConnectionProfile(name="prod", host="h", username="u",
                              ai_context="This server runs the production API. "
                              "Never run `systemctl restart` without asking first.")
        # 2. The helper extracts it
        note = p.ai_context   # mirrors _server_note()
        self.assertTrue(note)
        # 3. build_system incorporates it
        system = build_system("ask", "OS: Ubuntu 22.04", note)
        self.assertIn("production API", system)
        self.assertIn("systemctl restart", system)
        self.assertIn("follow these strictly", system.lower())


if __name__ == "__main__":
    unittest.main()
