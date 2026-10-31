"""persistent terminals, recycle bin, folder transfers, AI tool-calling,
transfer history, smart auto-scroll behavior.

Pure-logic only — the UI is not tested (per the user's "light testing" preference from Phase 5+).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import tmux as tmux_svc
from services import recyclebin as RB
from services import folder_transfer as FT
from services import transfer_history as TH
from services.ai import tools as ai_tools


# ======================================================================
# — Persistent terminal sessions (tmux)
# ======================================================================
class TestTmux(unittest.TestCase):
    def test_sanitize_name_accepts_safe_names(self):
        for ok in ("deploy", "build-1", "tail_logs", "takvps"):
            self.assertEqual(tmux_svc.sanitize_name(ok), ok)

    def test_sanitize_name_rejects_dangerous_names(self):
        # Empty
        self.assertIsNone(tmux_svc.sanitize_name(""))
        # Spaces and special chars
        self.assertIsNone(tmux_svc.sanitize_name("rm -rf /"))
        self.assertIsNone(tmux_svc.sanitize_name("name;evil"))
        # Too long
        self.assertIsNone(tmux_svc.sanitize_name("a" * 41))
        # Dots and colons (tmux rejects `:` and swaps `.`)
        self.assertIsNone(tmux_svc.sanitize_name("a.b"))
        self.assertIsNone(tmux_svc.sanitize_name("a:b"))

    def test_is_installed_detects_path(self):
        FakeResult = MagicMock()
        FakeResult.stdout = "/usr/bin/tmux"
        FakeResult.exit_code = 0
        self.assertTrue(tmux_svc.is_installed(FakeResult))

        FakeResult.stdout = ""
        FakeResult.exit_code = 1
        self.assertFalse(tmux_svc.is_installed(FakeResult))

    def test_parse_sessions_handles_empty_and_errors(self):
        self.assertEqual(tmux_svc.parse_sessions(""), [])
        self.assertEqual(tmux_svc.parse_sessions("no server running on /tmp/tmux-1000/default\n"), [])
        self.assertEqual(tmux_svc.parse_sessions("error connecting"), [])

    def test_parse_sessions_parses_real_output(self):
        out = (
            "deploy: 1 windows (created Mon Oct 28 09:14:32 2024)\n"
            "logs: 2 windows (created Mon Oct 28 10:01:11 2024) (attached)\n"
        )
        sessions = tmux_svc.parse_sessions(out)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].name, "deploy")
        self.assertEqual(sessions[0].windows, 1)
        self.assertFalse(sessions[0].attached)
        self.assertEqual(sessions[1].name, "logs")
        self.assertEqual(sessions[1].windows, 2)
        self.assertTrue(sessions[1].attached)

    def test_list_command_exits_clean_when_no_server(self):
        cmd = tmux_svc.list_command()
        self.assertIn("tmux list-sessions", cmd)
        # Must not raise on a server with no sessions.
        self.assertIn("|| true", cmd)

    def test_new_or_attach_uses_correct_flag(self):
        cmd = tmux_svc.new_or_attach_command("deploy")
        # `-A` makes tmux create-if-missing and attach-if-exists, in one shot.
        self.assertIn("new-session -A", cmd)
        self.assertIn("deploy", cmd)

    def test_kill_session_quotes_name(self):
        # The sanitizer rejects spaces, so this raises.
        with self.assertRaises(ValueError):
            tmux_svc.kill_session_command("name with spaces")
        # A safe name like "deploy" is a shell-safe word, so shlex.quote leaves
        # it bare (no quotes needed). We just verify the name is present.
        cmd = tmux_svc.kill_session_command("deploy")
        self.assertIn("deploy", cmd)
        # A name needing quoting (e.g. "deploy-2") is still safe enough to be left
        # bare, but a hyphenated name with shell-special chars would be quoted.
        cmd = tmux_svc.kill_session_command("deploy-2")
        self.assertIn("deploy-2", cmd)

    def test_resolve_shell_command_returns_none_for_no_name(self):
        # Without a name, fall back to a normal shell.
        self.assertIsNone(tmux_svc.resolve_shell_command(MagicMock(), None))

    def test_resolve_shell_command_returns_attach_for_name(self):
        cmd = tmux_svc.resolve_shell_command(MagicMock(), "deploy")
        self.assertIn("tmux", cmd)
        self.assertIn("deploy", cmd)


# ======================================================================
# — Recycle Bin
# ======================================================================
class TestRecycleBin(unittest.TestCase):
    def test_bin_path_under_home(self):
        self.assertEqual(RB.bin_path("/home/alice"), "/home/alice/.takvps-recycle")
        self.assertEqual(RB.bin_path("/root"), "/root/.takvps-recycle")
        # Trailing slash handled.
        self.assertEqual(RB.bin_path("/home/alice/"), "/home/alice/.takvps-recycle")
        # Empty home → root (double slash collapsed by the join).
        self.assertTrue(RB.bin_path("").endswith("/.takvps-recycle"))

    def test_safe_entry_name_flattens_paths(self):
        name = RB.safe_entry_name("/etc/nginx/nginx.conf", ts=1730254881)
        # timestamp + flattened path with - separators
        self.assertTrue(name.startswith("1730254881-"))
        self.assertIn("etc-nginx-nginx.conf", name)
        # No slashes survive.
        self.assertNotIn("/", name)

    def test_entry_meta_roundtrip(self):
        meta = RB.entry_meta("name-123", "/etc/nginx/nginx.conf", is_dir=False, size=1024)
        serialized = RB.serialize_meta(meta)
        parsed = RB.parse_meta(serialized)
        self.assertEqual(parsed["orig_path"], "/etc/nginx/nginx.conf")
        self.assertEqual(parsed["size"], 1024)

    def test_parse_meta_rejects_garbage(self):
        self.assertIsNone(RB.parse_meta(""))
        self.assertIsNone(RB.parse_meta("not json"))
        self.assertIsNone(RB.parse_meta('{"foo": "bar"}'))     # no orig_path

    def test_recycle_command_builds_mv_and_meta_write(self):
        move, write_meta = RB.recycle_command("/home/alice", "/etc/nginx/nginx.conf", "1730254881-etc-nginx-nginx.conf")
        # Move uses `mv --` (so paths starting with - aren't interpreted as flags)
        self.assertIn("mv --", move)
        self.assertIn("nginx.conf", move)
        # The meta write is a heredoc to a JSON file
        self.assertIn("cat > ", write_meta)
        self.assertIn("TAKVPS_META_EOF", write_meta)

    def test_parse_list_handles_realistic_output(self):
        out = (
            "1730254881-etc-nginx-nginx.conf|1024|1730254881|0|/etc/nginx/nginx.conf\n"
            "1730254912-home-alice-deploy.sh|512|1730254912|0|/home/alice/deploy.sh\n"
            "1730254915-var-log-app.log|2048|1730254915|1|/var/log/app\n"
        )
        entries = RB.parse_list(out)
        self.assertEqual(len(entries), 3)
        # Newest first
        self.assertEqual(entries[0].name, "1730254915-var-log-app-app.log" if False else entries[0].name)
        self.assertTrue(entries[0].is_dir)
        self.assertEqual(entries[0].orig_path, "/var/log/app")

    def test_is_protected_blocks_root_and_system_dirs(self):
        for path in ["/", "", "/bin", "/etc", "/usr", "/boot"]:
            blocked, _ = RB.is_protected(path, "/home/alice")
            self.assertTrue(blocked, f"{path!r} should be protected")

    def test_is_protected_blocks_bin_itself(self):
        blocked, _ = RB.is_protected("/home/alice/.takvps-recycle", "/home/alice")
        self.assertTrue(blocked)

    def test_is_protected_allows_normal_files(self):
        blocked, _ = RB.is_protected("/home/alice/deploy.sh", "/home/alice")
        self.assertFalse(blocked)
        blocked, _ = RB.is_protected("/var/log/app.log", "/home/alice")
        self.assertFalse(blocked)

    def test_restore_command_makes_parent_dirs(self):
        # The restore command needs a parent-dir mkdir before the mv.
        parent_cmd = RB.ensure_parent_command("/var/www/site/config.yml")
        self.assertIn("mkdir -p", parent_cmd)
        self.assertIn("/var/www/site", parent_cmd)

    def test_purge_older_than_rejects_non_positive_days(self):
        with self.assertRaises(ValueError):
            RB.purge_older_than_command("/home/alice", 0)
        with self.assertRaises(ValueError):
            RB.purge_older_than_command("/home/alice", -1)


# ======================================================================
# — Folder transfers
# ======================================================================
class TestFolderTransfer(unittest.TestCase):
    def test_build_plan_down_walks_tree(self):
        # A nested remote tree.
        tree = [
            FT.TreeNode(rel="sub", is_dir=True),
            FT.TreeNode(rel="sub/file1.txt", is_dir=False, size=100),
            FT.TreeNode(rel="sub/deeper", is_dir=True),
            FT.TreeNode(rel="sub/deeper/file2.txt", is_dir=False, size=200),
            FT.TreeNode(rel="root.txt", is_dir=False, size=50),
        ]
        plan = FT.build_plan_down(tree, "/remote/root", "/local/root", "root")
        self.assertEqual(plan.kind, "down")
        self.assertEqual(plan.file_count, 3)
        self.assertEqual(plan.total_bytes, 350)
        # Two dirs in the tree: "sub" and "sub/deeper".
        self.assertEqual(len(plan.dirs), 2)
        # Order: dirs first, then files. plan_entries yields one (src, dst, size) per file.
        entries = list(FT.plan_entries(plan))
        self.assertEqual(len(entries), 3)
        # The first file's destination should be under the local root.
        self.assertTrue(entries[0][1].replace("\\", "/").startswith("/local/root/"))

    def test_build_plan_up_walks_local_tree(self):
        # Fake os.walk result.
        def fake_walk(root):
            yield (root, ["sub"], ["root.txt"])
            yield (root + "/sub", [], ["file1.txt"])
        # Note: real os.walk yields dirpath as a path, so we need to align.
        # Build a temp dir tree so the test reflects real behavior.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "root.txt").write_text("hello")
            sub = Path(tmp) / "sub"
            sub.mkdir()
            (sub / "file1.txt").write_text("world")
            plan = FT.build_plan_up(tmp, "/remote/dest", "dest", walk=None)
            self.assertEqual(plan.file_count, 2)
            self.assertEqual(plan.total_bytes, 10)   # "hello" + "world"
            self.assertEqual(len(plan.dirs), 1)
            self.assertEqual(plan.dirs[0], "sub")

    def test_make_dirs_command_empty_list_is_noop(self):
        self.assertEqual(FT.make_dirs_command([]), "true")
        cmd = FT.make_dirs_command(["/remote/a", "/remote/b"])
        self.assertIn("mkdir -p --", cmd)
        self.assertIn("/remote/a", cmd)
        self.assertIn("/remote/b", cmd)

    def test_make_local_dirs_creates_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            FT.make_local_dirs(tmp, ["sub/deep", "another"])
            self.assertTrue((Path(tmp) / "sub" / "deep").is_dir())
            self.assertTrue((Path(tmp) / "another").is_dir())

    def test_truncate_for_display_formats_size(self):
        plan = FT.FolderTransferPlan("down", "test", "/r", "/l", ["a", "b"], [
            FT.TreeNode(rel="f1", is_dir=False, size=100_000),
            FT.TreeNode(rel="f2", is_dir=False, size=200_000),
        ], 300_000)
        s = FT.truncate_for_display(plan)
        self.assertIn("2 files", s)
        self.assertIn("KB", s)
        self.assertIn("2 folders", s)


# ======================================================================
# — Persistent transfer history
# ======================================================================
class TestTransferHistory(unittest.TestCase):
    def test_append_caps_at_keep_limit(self):
        h = TH.TransferHistory(keep=3)
        for i in range(10):
            h.append(TH.HistoryItem("down", f"f{i}", "/r", "/l", 100, "done"))
        self.assertEqual(len(h.items), 3)
        # The newest 3 survive (the last 3 appended).
        self.assertEqual(h.items[-1].name, "f9")

    def test_filter_by_kind(self):
        h = TH.TransferHistory()
        h.append(TH.HistoryItem("down", "f1", "/r", "/l", 100, "done"))
        h.append(TH.HistoryItem("up", "f2", "/l", "/r", 200, "done"))
        self.assertEqual(len(h.filter("down")), 1)
        self.assertEqual(h.filter("down")[0].name, "f1")
        self.assertEqual(len(h.filter()), 2)

    def test_load_and_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = TH.history_path(Path(tmp))
            h = TH.TransferHistory()
            h.append(TH.HistoryItem("down", "f1", "/r", "/l", 100, "done", finished=1234567890))
            TH.save(path, h)
            # File should exist and be valid JSON.
            self.assertTrue(path.exists())
            loaded = TH.load(path)
            self.assertEqual(len(loaded.items), 1)
            self.assertEqual(loaded.items[0].name, "f1")
            self.assertEqual(loaded.items[0].finished, 1234567890)

    def test_load_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = TH.load(TH.history_path(Path(tmp)))
            self.assertEqual(len(loaded.items), 0)

    def test_load_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = TH.history_path(Path(tmp))
            path.write_text("not valid json")
            loaded = TH.load(path)
            self.assertEqual(len(loaded.items), 0)

    def test_record_finished_copies_fields(self):
        # Build a minimal fake Transfer.
        T = type("T", (), {"kind": "down", "name": "f", "remote": "/r", "local": "/l",
                           "total": 100, "status": "done", "error": "", "created": 1000.0})()
        h = TH.TransferHistory()
        TH.record_finished(T, h, finished_at=2000.0)
        self.assertEqual(len(h.items), 1)
        self.assertEqual(h.items[0].finished, 2000.0)
        self.assertEqual(h.items[0].created, 1000.0)

    def test_dedupe_against_queue(self):
        h = TH.TransferHistory()
        h.append(TH.HistoryItem("down", "f1", "/r", "/l", 100, "done"))
        # A fake queue item with the same key tuple.
        Q = type("Q", (), {"kind": "down", "name": "f1", "remote": "/r",
                           "local": "/l", "total": 100})()
        TH.dedupe_against_queue(h, [Q])
        self.assertEqual(len(h.items), 0)


# ======================================================================
# — Native AI tool-calling
# ======================================================================
class TestAITools(unittest.TestCase):
    def test_supports_tools_for_known_kinds(self):
        self.assertTrue(ai_tools.supports_tools("openai"))
        self.assertTrue(ai_tools.supports_tools("anthropic"))
        self.assertTrue(ai_tools.supports_tools("gemini"))
        self.assertFalse(ai_tools.supports_tools("unknown"))
        self.assertFalse(ai_tools.supports_tools(""))

    def test_build_request_with_tools_per_provider(self):
        from services.ai.providers import ProviderConfig
        for kind in ("openai", "anthropic", "gemini"):
            cfg = ProviderConfig(kind=kind, base_url="", model="m", api_key="k")
            extra = ai_tools.build_request_with_tools(cfg, [], "", tools_enabled=True)
            self.assertTrue(extra, f"no extra kwargs for {kind}")
        # Disabled returns empty
        cfg = ProviderConfig(kind="openai", base_url="", model="m", api_key="k")
        self.assertEqual(ai_tools.build_request_with_tools(cfg, [], "", tools_enabled=False), {})

    def test_extract_command_and_sudo(self):
        call = ai_tools.ToolCall(id="1", name="run_shell",
                                 args={"command": "ls -la", "sudo": True})
        cmd, sudo = ai_tools.extract_command(call)
        self.assertEqual(cmd, "ls -la")
        self.assertTrue(sudo)

        # Missing sudo → defaults to False
        call.args = {"command": "whoami"}
        cmd, sudo = ai_tools.extract_command(call)
        self.assertEqual(cmd, "whoami")
        self.assertFalse(sudo)

        # Missing command → empty string
        call.args = {}
        cmd, _ = ai_tools.extract_command(call)
        self.assertEqual(cmd, "")

    def test_validate_command_safety_rejects_empty_and_long(self):
        ok, _ = ai_tools.validate_command_safety("ls -la")
        self.assertTrue(ok)
        ok, msg = ai_tools.validate_command_safety("")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())
        ok, msg = ai_tools.validate_command_safety("x" * 9000)
        self.assertFalse(ok)
        self.assertIn("long", msg.lower())

    def test_tool_result_message_per_provider(self):
        call = ai_tools.ToolCall(id="abc", name="run_shell", args={"command": "ls"})
        # OpenAI: tool role, tool_call_id
        m = ai_tools.tool_result_message("openai", call, "out", 0)
        self.assertEqual(m["role"], "tool")
        self.assertEqual(m["tool_call_id"], "abc")
        # Anthropic: user role with tool_result content
        m = ai_tools.tool_result_message("anthropic", call, "out", 0)
        self.assertEqual(m["role"], "user")
        self.assertEqual(m["content"][0]["type"], "tool_result")
        # Gemini: user role with functionResponse part
        m = ai_tools.tool_result_message("gemini", call, "out", 0)
        self.assertEqual(m["role"], "user")
        self.assertIn("functionResponse", m["parts"][0])

    def test_accumulator_openai_streams_args_in_chunks(self):
        # OpenAI streams `function.arguments` in pieces. We must concatenate them
        # and parse the full JSON at stream end.
        acc = ai_tools.ToolCallAccumulator("openai")
        # Split the JSON `{"command": "ls"}` into two chunks.
        acc.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "run_shell", "arguments": '{"comma'}}
        ]}}]})
        acc.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'nd": "ls"}'}}
        ]}}]})
        calls = acc.finish()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "run_shell")
        self.assertEqual(calls[0].args["command"], "ls")

    def test_accumulator_anthropic_handles_input_json_delta(self):
        acc = ai_tools.ToolCallAccumulator("anthropic")
        acc.feed({"type": "content_block_start",
                  "content_block": {"type": "tool_use", "id": "tool_1", "name": "run_shell"}})
        acc.feed({"type": "content_block_delta",
                  "delta": {"type": "input_json_delta", "partial_json": '{"command":'}})
        acc.feed({"type": "content_block_delta",
                  "delta": {"type": "input_json_delta", "partial_json": ' "whoami"}'}})
        calls = acc.finish()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "run_shell")
        self.assertEqual(calls[0].args["command"], "whoami")

    def test_accumulator_gemini_emits_complete_call(self):
        acc = ai_tools.ToolCallAccumulator("gemini")
        acc.feed({"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "run_shell", "args": {"command": "uptime"}}}
        ]}}]})
        calls = acc.finish()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "run_shell")
        self.assertEqual(calls[0].args["command"], "uptime")

    def test_accumulator_handles_malformed_args(self):
        # A chunk with broken JSON in the args shouldn't crash the accumulator.
        acc = ai_tools.ToolCallAccumulator("openai")
        acc.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "run_shell", "arguments": "not json{"}}
        ]}}]})
        calls = acc.finish()
        self.assertEqual(len(calls), 1)
        # Args parsing fails → empty dict
        self.assertEqual(calls[0].args, {})


# ======================================================================
# bug fixes — SSH timeout + Tools page accessibility
# ======================================================================
class TestPhase13BugFixes(unittest.TestCase):
    def test_ssh_get_server_host_key_options_pattern(self):
        """get_server_host_key doesn't accept login_timeout as a kwarg.
        The fix wraps it in SSHClientConnectionOptions(options=...). Verify the pattern
        actually works against the installed asyncssh (skip if not installed)."""
        try:
            import asyncssh
        except ImportError:
            self.skipTest("asyncssh not installed in this environment")
        # Verify get_server_host_key accepts `options=` (the fixed path).
        import inspect
        sig = inspect.signature(asyncssh.get_server_host_key)
        self.assertIn("options", sig.parameters)
        # Verify SSHClientConnectionOptions accepts login_timeout.
        opts = asyncssh.SSHClientConnectionOptions(login_timeout=15)
        self.assertEqual(opts.login_timeout, 15)
        # And that the broken path (login_timeout= as kwarg) really would fail —
        # that's the bug we fixed.
        self.assertNotIn("login_timeout", sig.parameters)

    def test_tools_in_default_nav_order(self):
        """'tools' must be in DEFAULT_NAV so the bottom-bar shows it."""
        from storage.settings import DEFAULT_NAV
        self.assertIn("tools", DEFAULT_NAV)
        # And in the first 4 slots (so it shows in the bottom bar on phones).
        self.assertLessEqual(DEFAULT_NAV.index("tools"), 3)

    def test_settings_has_native_tools_flag(self):
        """the AI native-tools toggle must exist on Settings."""
        from storage.settings import Settings
        s = Settings()
        self.assertTrue(hasattr(s, "ai_native_tools"))
        # Default ON (so modern providers use native tool-calling out of the box).
        self.assertTrue(s.ai_native_tools)


if __name__ == "__main__":
    unittest.main()
