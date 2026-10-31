"""tool-call inspection formatter.

Tests the pure `services/tool_inspect.py` helper and the new `Msg.tool_calls`
field. No Flet imports.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tool_inspect import (count_tool_calls, format_tool_call, format_tool_calls,
                                    has_tool_calls, summary)
from services.ai.chat import Msg


# ======================================================================
# Part 8 — format_tool_call
# ======================================================================
class TestFormatToolCall(unittest.TestCase):
    def test_basic_call(self):
        call = {"id": "call_1", "name": "run_shell",
                "args": {"command": "uptime", "sudo": False}}
        out = format_tool_call(call)
        self.assertIn("run_shell", out)
        self.assertIn("[call_1]", out)
        self.assertIn("command: uptime", out)
        self.assertIn("sudo: false", out)

    def test_with_result(self):
        call = {"id": "call_1", "name": "run_shell",
                "args": {"command": "ls"},
                "result_text": "total 8\ndrwxr-xr-x", "result_exit": 0}
        out = format_tool_call(call)
        self.assertIn("→ exit 0", out)
        self.assertIn("output:", out)
        self.assertIn("total 8", out)

    def test_without_result(self):
        call = {"id": "call_1", "name": "run_shell",
                "args": {"command": "ls"}}
        out = format_tool_call(call, include_result=False)
        self.assertNotIn("→ exit", out)
        self.assertNotIn("output:", out)

    def test_long_command_truncated(self):
        long_cmd = "x" * 500
        call = {"id": "c", "name": "run_shell", "args": {"command": long_cmd}}
        out = format_tool_call(call)
        self.assertIn("…", out)     # the truncation marker
        # The full command shouldn't appear.
        self.assertNotIn("x" * 500, out)

    def test_long_result_truncated(self):
        long_result = "line\n" * 200
        call = {"id": "c", "name": "run_shell", "args": {"command": "x"},
                "result_text": long_result, "result_exit": 0}
        out = format_tool_call(call)
        # The 200-line result is capped at 20 lines (splitlines()[:20]).
        self.assertLessEqual(out.count("line"), 20)

    def test_missing_id(self):
        call = {"name": "run_shell", "args": {"command": "x"}}
        out = format_tool_call(call)
        self.assertIn("run_shell", out)
        self.assertNotIn("[", out)     # no id bracket

    def test_missing_args(self):
        call = {"id": "c", "name": "run_shell"}
        out = format_tool_call(call)
        self.assertIn("run_shell", out)

    def test_bool_arg_formatted_as_true_false(self):
        call = {"id": "c", "name": "run_shell", "args": {"sudo": True}}
        out = format_tool_call(call)
        self.assertIn("sudo: true", out)
        self.assertNotIn("True", out)     # not Python's True

    def test_dict_arg_as_json(self):
        call = {"id": "c", "name": "run_shell",
                "args": {"options": {"flag": 1}}}
        out = format_tool_call(call)
        self.assertIn('{"flag": 1}', out)

    def test_declined_result(self):
        call = {"id": "c", "name": "run_shell",
                "args": {"command": "rm -rf /"},
                "result_text": "The user declined to run this command.",
                "result_exit": None}
        out = format_tool_call(call)
        # result_exit is None — the "→ exit" line should not appear.
        self.assertNotIn("→ exit", out)
        self.assertIn("declined", out.lower())


# ======================================================================
# Part 8 — format_tool_calls (list)
# ======================================================================
class TestFormatToolCalls(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(format_tool_calls([]), "")
        self.assertEqual(format_tool_calls(None), "")

    def test_single_call(self):
        calls = [{"id": "c1", "name": "run_shell", "args": {"command": "ls"}}]
        out = format_tool_calls(calls)
        self.assertIn("run_shell", out)
        self.assertIn("command: ls", out)

    def test_multiple_calls_separated(self):
        calls = [
            {"id": "c1", "name": "run_shell", "args": {"command": "ls"}},
            {"id": "c2", "name": "run_shell", "args": {"command": "pwd"}},
        ]
        out = format_tool_calls(calls)
        self.assertIn("ls", out)
        self.assertIn("pwd", out)
        # Two calls separated by a blank line.
        self.assertIn("\n\n", out)


# ======================================================================
# Part 8 — count / has / summary
# ======================================================================
class TestCountHasSummary(unittest.TestCase):
    def test_count_empty(self):
        self.assertEqual(count_tool_calls([]), 0)
        self.assertEqual(count_tool_calls(None), 0)

    def test_count_nonempty(self):
        calls = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
        self.assertEqual(count_tool_calls(calls), 3)

    def test_has_tool_calls_with_msg(self):
        m = Msg(role="assistant", text="hi", tool_calls=[{"id": "c1"}])
        self.assertTrue(has_tool_calls(m))
        m2 = Msg(role="assistant", text="hi")
        self.assertFalse(has_tool_calls(m2))

    def test_has_tool_calls_with_dict(self):
        d = {"role": "assistant", "text": "hi", "tool_calls": [{"id": "c1"}]}
        self.assertTrue(has_tool_calls(d))
        d2 = {"role": "assistant", "text": "hi"}
        self.assertFalse(has_tool_calls(d2))

    def test_summary_singular(self):
        m = Msg(role="assistant", text="hi", tool_calls=[{"id": "c1"}])
        self.assertEqual(summary(m), "1 tool call")

    def test_summary_plural(self):
        m = Msg(role="assistant", text="hi",
                tool_calls=[{"id": "c1"}, {"id": "c2"}])
        self.assertEqual(summary(m), "2 tool calls")

    def test_summary_empty(self):
        m = Msg(role="assistant", text="hi")
        self.assertEqual(summary(m), "")


# ======================================================================
# Part 8 — Msg.tool_calls field + serialization
# ======================================================================
class TestMsgToolCallsField(unittest.TestCase):
    def test_default_empty(self):
        m = Msg(role="assistant", text="hi")
        self.assertEqual(m.tool_calls, [])

    def test_set_and_get(self):
        m = Msg(role="assistant", text="hi",
                tool_calls=[{"id": "c1", "name": "run_shell", "args": {"command": "ls"}}])
        self.assertEqual(len(m.tool_calls), 1)
        self.assertEqual(m.tool_calls[0]["name"], "run_shell")

    def test_to_dict_includes_tool_calls(self):
        m = Msg(role="assistant", text="hi", tool_calls=[{"id": "c1"}])
        d = m.to_dict()
        self.assertIn("tool_calls", d)
        self.assertEqual(d["tool_calls"], [{"id": "c1"}])

    def test_from_dict_includes_tool_calls(self):
        d = {"role": "assistant", "text": "hi", "tool_calls": [{"id": "c1"}]}
        m = Msg.from_dict(d)
        self.assertEqual(m.tool_calls, [{"id": "c1"}])

    def test_from_dict_without_tool_calls_defaults_empty(self):
        """Old saved messages (pre-Phase-14-Part-8) don't have a tool_calls field
        and must load with an empty list."""
        d = {"role": "assistant", "text": "hi"}
        m = Msg.from_dict(d)
        self.assertEqual(m.tool_calls, [])

    def test_roundtrip_preserves_tool_calls(self):
        m = Msg(role="assistant", text="hi",
                tool_calls=[{"id": "c1", "name": "run_shell", "args": {"command": "ls", "sudo": True}}])
        d = m.to_dict()
        m2 = Msg.from_dict(d)
        self.assertEqual(m2.tool_calls, m.tool_calls)
        self.assertEqual(m2.tool_calls[0]["args"]["sudo"], True)


# ======================================================================
# Part 8 — end-to-end formatting
# ======================================================================
class TestEndToEnd(unittest.TestCase):
    def test_full_request_result_cycle(self):
        """A realistic tool-call cycle: model proposes a command, it runs,
        the result is attached."""
        calls = [{
            "id": "call_abc123",
            "name": "run_shell",
            "args": {"command": "df -h", "sudo": False},
            "result_text": "Filesystem  Size  Used  Avail  Use%\n/dev/sda1   20G   8G    12G    40%",
            "result_exit": 0,
        }]
        out = format_tool_calls(calls)
        # All parts present.
        self.assertIn("run_shell", out)
        self.assertIn("[call_abc123]", out)
        self.assertIn("command: df -h", out)
        self.assertIn("sudo: false", out)
        self.assertIn("→ exit 0", out)
        self.assertIn("output:", out)
        self.assertIn("Filesystem", out)
        self.assertIn("40%", out)

    def test_multiple_calls_in_one_message(self):
        """An agent step that calls two tools: `uptime` then `free -m`."""
        calls = [
            {"id": "c1", "name": "run_shell", "args": {"command": "uptime"},
             "result_text": "load average: 0.5", "result_exit": 0},
            {"id": "c2", "name": "run_shell", "args": {"command": "free -m"},
             "result_text": "Mem: 2048 1024 1024", "result_exit": 0},
        ]
        m = Msg(role="assistant", text="Checking memory...", tool_calls=calls)
        self.assertEqual(summary(m), "2 tool calls")
        out = format_tool_calls(m.tool_calls)
        self.assertIn("uptime", out)
        self.assertIn("free -m", out)
        self.assertIn("load average", out)
        self.assertIn("Mem:", out)


if __name__ == "__main__":
    unittest.main()
