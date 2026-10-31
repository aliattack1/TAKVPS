"""Phase 14 Part 8 — Tool-call inspection formatter.

Pure helper that converts a list of tool-call dicts (as stored on `Msg.tool_calls`)
into a human-readable, collapsible text block for the chat UI's "Show raw tool
calls" toggle. No Flet imports — the UI calls `format_tool_calls()` and renders
the result in a monospace `Text`.

The tool-call dicts have the shape:
    {"id": "call_1", "name": "run_shell", "args": {"command": "ls", "sudo": false},
     "result_text": "...", "result_exit": 0}

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

import json
from typing import Sequence


def format_tool_call(call: dict, include_result: bool = True) -> str:
    """Format a single tool-call dict as a readable text block.

    Example output:
        run_shell  [call_1]
          command: ls
          sudo: false
          → exit 0
          output (first 200 chars):
            total 8
            drwxr-xr-x 2 root root ...
    """
    name = call.get("name", "?")
    call_id = call.get("id", "")
    args = call.get("args", {}) or {}
    header = f"{name}"
    if call_id:
        header += f"  [{call_id}]"
    lines = [header]
    # Render args as key: value pairs, with JSON for nested values.
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + "…"
        lines.append(f"  {k}: {_format_value(v)}")
    if include_result:
        result_text = call.get("result_text", "")
        result_exit = call.get("result_exit")
        if result_exit is not None:
            lines.append(f"  → exit {result_exit}")
        if result_text:
            # Cap the shown output to keep the block readable.
            shown = result_text[:500]
            if len(result_text) > 500:
                shown += f"\n… ({len(result_text) - 500} more chars)"
            lines.append("  output:")
            for ln in shown.splitlines()[:20]:
                lines.append(f"    {ln}")
    return "\n".join(lines)


def _format_value(v) -> str:
    """Format a single arg value: strings as-is, dicts/lists as compact JSON."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def format_tool_calls(calls: Sequence[dict], include_results: bool = True) -> str:
    """Format a list of tool-call dicts as a single text block.

    Each call is separated by a blank line. Returns "" if the list is empty.
    """
    if not calls:
        return ""
    return "\n\n".join(format_tool_call(c, include_result=include_results) for c in calls)


def count_tool_calls(calls: Sequence[dict]) -> int:
    """Number of tool calls. Defensive against None / non-list values."""
    if not calls:
        return 0
    return len(calls)


def has_tool_calls(msg) -> bool:
    """True if `msg.tool_calls` is non-empty. Works with Msg dataclass or a dict."""
    tc = getattr(msg, "tool_calls", None)
    if tc is None:
        tc = msg.get("tool_calls") if isinstance(msg, dict) else []
    return bool(tc)


def summary(msg) -> str:
    """One-line summary for the tool-call badge: '2 tool calls' or '1 tool call'."""
    n = count_tool_calls(getattr(msg, "tool_calls", None) or
                         (msg.get("tool_calls") if isinstance(msg, dict) else []))
    if n == 0:
        return ""
    return f"{n} tool call{'s' if n != 1 else ''}"
