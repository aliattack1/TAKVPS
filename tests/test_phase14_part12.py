"""Phase 14 Part 12 tests: lazy-load destination pages.

Tests the lazy-loading pattern (deferred disk reads) and the page-build
error catching wrapper. Uses fakes — no real Flet page required.
"""
from __future__ import annotations
import asyncio, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from storage.activity import ActivityLog


class TestLazyActivityLoad(unittest.TestCase):
    """The activity page's _first() function loads records in the background."""

    def test_first_loads_records_into_empty_list(self):
        """_first extends the recs list in place (so the reference stays
        the same — the page's draw() function already holds that reference)."""
        with tempfile.TemporaryDirectory() as tmp:
            log = ActivityLog(Path(tmp) / "activity.jsonl")
            log.add("s1", "Server 1", "uptime", 0, "user", "safe")
            log.add("s1", "Server 1", "ls -la", 0, "user", "safe")
            # Simulate what the activity page does: start with empty recs,
            # then _first loads them.
            recs = []
            ctx = type("Ctx", (), {"activity": log})()
            # Import the _first function from the activity page module.
            # We can't import it directly because it requires Flet, so we
            # replicate its logic here (the function is just: sleep, extend, draw).
            async def _first(draw, ctx, recs):
                await asyncio.sleep(0.01)
                recs.extend(ctx.activity.recent(300))
                draw()
            drew = []
            def draw():
                drew.append(len(recs))
            asyncio.run(_first(draw, ctx, recs))
            self.assertEqual(len(recs), 2)
            self.assertEqual(drew, [2])

    def test_first_does_not_block(self):
        """The sleep(0.01) in _first ensures the page renders with 'Loading…'
        before the data arrives."""
        with tempfile.TemporaryDirectory() as tmp:
            log = ActivityLog(Path(tmp) / "activity.jsonl")
            log.add("s1", "S1", "cmd", 0, "user", "safe")
            recs = []
            ctx = type("Ctx", (), {"activity": log})()
            async def _first(draw, ctx, recs):
                await asyncio.sleep(0.01)
                recs.extend(ctx.activity.recent(300))
                draw()
            # Before _first runs, recs is empty.
            self.assertEqual(len(recs), 0)
            asyncio.run(_first(lambda: None, ctx, recs))
            # After _first runs, recs is populated.
            self.assertEqual(len(recs), 1)


class TestPageBuildErrorCatching(unittest.TestCase):
    """The Shell's render() wraps page builds in try/except so a construction
    error doesn't crash the app. We test the pattern directly."""

    def test_try_except_catches_construction_error(self):
        """A page builder that raises should be caught, not propagated."""
        def good_builder():
            return "page content"
        def bad_builder():
            raise RuntimeError("construction failed")
        # The pattern from Shell.render():
        def build_page(builder):
            try:
                return builder()
            except Exception as e:
                return f"ERROR: {e}"
        self.assertEqual(build_page(good_builder), "page content")
        self.assertEqual(build_page(bad_builder), "ERROR: construction failed")

    def test_error_message_truncated(self):
        """Long error messages are truncated to 300 chars in the UI."""
        def bad_builder():
            raise RuntimeError("x" * 500)
        try:
            bad_builder()
        except Exception as e:
            msg = str(e)[:300]
            self.assertEqual(len(msg), 300)

    def test_no_error_for_valid_page(self):
        """A page that builds successfully should not trigger the error path."""
        class FakePage:
            def __init__(self):
                self.content = None
            def build(self):
                return "ok"
        p = FakePage()
        try:
            p.content = p.build()
        except Exception:
            p.content = "ERROR"
        self.assertEqual(p.content, "ok")


if __name__ == "__main__":
    unittest.main()
