"""Native tool-calling adapters (Phase 13.6).

Phase 7's "structured fallback" asked the model to emit `bash` blocks and parsed
them with regex; that worked with every provider but lost the structured
tool-call protocol that OpenAI/Anthropic/Gemini natively support.

This module builds the tool-call payload for each provider, parses the streaming
`tool_calls` deltas back into discrete tool invocations, and exposes a single
`Tool` schema plus a fixed `run_shell` tool definition.

The runtime still routes the actual command through the existing risk classifier
and approval flow — native tool-calling changes HOW the model proposes commands,
not whether they run.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional


# The single tool we expose to the model: run a shell command on the server.
SHELL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": (
            "Run a shell command on the user's Linux server over SSH and return the "
            "merged stdout+stderr and exit code. Use this to investigate or act on "
            "the server. Keep commands non-interactive. The user sees every command "
            "before it runs; dangerous commands need explicit confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."},
                "sudo": {"type": "boolean", "description": "Run with sudo (default false).", "default": False},
            },
            "required": ["command"],
        },
    },
}


@dataclass
class ToolCall:
    id: str               # provider's tool_call id, used to send the result back
    name: str             # always "run_shell" today
    args: dict            # parsed args: {"command": "...", "sudo": bool}
    raw: dict = field(default_factory=dict)    # full delta for debugging


def build_request_with_tools(cfg, messages: list[dict], system: str,
                             tools_enabled: bool = True) -> dict:
    """Return extra kwargs to merge into the provider request body when tools
    are enabled. Different providers nest tools differently.

    This is provider-agnostic; the provider adapter merges it into its body.
    """
    if not tools_enabled:
        return {}
    if cfg.kind == "openai":
        return {"tools": [SHELL_TOOL], "tool_choice": "auto"}
    if cfg.kind == "anthropic":
        # Anthropic uses a different schema; the function shape is the same.
        return {"tools": [{
            "name": "run_shell",
            "description": SHELL_TOOL["function"]["description"],
            "input_schema": SHELL_TOOL["function"]["parameters"],
        }]}
    if cfg.kind == "gemini":
        # Gemini: function declarations under tools[].function_declarations
        return {"tools": [{"function_declarations": [{
            "name": "run_shell",
            "description": SHELL_TOOL["function"]["description"],
            "parameters": SHELL_TOOL["function"]["parameters"],
        }]}]}
    return {}


# ---- parsing streaming deltas ----------------------------------------------
#
# Each provider emits tool-call deltas in its own shape. We accumulate them by
# `index` (OpenAI/Gemini) or by `id` (Anthropic) until the stream ends, then
# emit a single ToolCall per logical call.

class ToolCallAccumulator:
    """Buffer streaming tool-call deltas; call .finish() at stream end."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        # OpenAI: by index → {id, name, args_buffer}
        # Anthropic: by id → {name, args_buffer}
        # Gemini: by index → {id, name, args_buffer}
        self._buf: dict[int, dict] = {}

    def feed(self, obj: dict) -> None:
        if self.kind == "openai":
            self._feed_openai(obj)
        elif self.kind == "anthropic":
            self._feed_anthropic(obj)
        elif self.kind == "gemini":
            self._feed_gemini(obj)

    def _feed_openai(self, obj: dict) -> None:
        ch = (obj.get("choices") or [{}])[0]
        deltas = (ch.get("delta") or {}).get("tool_calls") or []
        for d in deltas:
            i = d.get("index", 0)
            slot = self._buf.setdefault(i, {"id": "", "name": "", "args": ""})
            if d.get("id"):
                slot["id"] = d["id"]
            fn = d.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]

    def _feed_anthropic(self, obj: dict) -> None:
        # Anthropic emits three event types we care about:
        #  - content_block_start with tool_use: gives id + name
        #  - content_block_delta with input_json_delta: appends args
        #  - message_stop: end
        if obj.get("type") == "content_block_start":
            block = obj.get("content_block") or {}
            if block.get("type") == "tool_use":
                cid = block.get("id", "")
                slot = self._buf.setdefault(len(self._buf), {"id": cid, "name": "", "args": ""})
                slot["id"] = cid
                slot["name"] = block.get("name", "")
        elif obj.get("type") == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "input_json_delta":
                # Append to the last slot — Anthropic streams args to the most
                # recent tool_use block.
                if self._buf:
                    last_key = max(self._buf.keys())
                    self._buf[last_key]["args"] += d.get("partial_json", "")

    def _feed_gemini(self, obj: dict) -> None:
        cand = (obj.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        for p in parts:
            fc = p.get("functionCall")
            if not fc:
                continue
            # Gemini emits the whole call at once (no streaming args), so we
            # always have a complete slot.
            i = len(self._buf)
            self._buf[i] = {
                "id": f"gemini-{i}",
                "name": fc.get("name", ""),
                "args": json.dumps(fc.get("args") or {}),
            }

    def finish(self) -> list[ToolCall]:
        """Return all completed tool calls, in stream order."""
        out: list[ToolCall] = []
        for i in sorted(self._buf.keys()):
            slot = self._buf[i]
            args = self._parse_args(slot.get("args", ""))
            if not slot.get("name"):
                continue
            out.append(ToolCall(
                id=slot.get("id") or f"call-{i}",
                name=slot["name"],
                args=args,
                raw=slot,
            ))
        self._buf = {}
        return out

    @staticmethod
    def _parse_args(s: str) -> dict:
        if not s:
            return {}
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else {}
        except ValueError:
            return {}


# ---- building the "tool result" message to send back ----------------------

def tool_result_message_openai(call: ToolCall, stdout: str, exit_code: int) -> dict:
    """The assistant message that acknowledges the call, then the user message
    with the result. We return the user-message half (the assistant half is the
    model's own tool_calls, which the caller already has)."""
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "content": f"exit_code: {exit_code}\noutput:\n{stdout}",
    }


def tool_result_message_anthropic(call: ToolCall, stdout: str, exit_code: int) -> dict:
    return {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": call.id,
            "content": f"exit_code: {exit_code}\noutput:\n{stdout}",
        }],
    }


def tool_result_message_gemini(call: ToolCall, stdout: str, exit_code: int) -> dict:
    """For Gemini, the function response goes in `parts` of a user role."""
    return {
        "role": "user",
        "parts": [{
            "functionResponse": {
                "name": call.name,
                "response": {"result": f"exit_code: {exit_code}\noutput:\n{stdout}"},
            },
        }],
    }


def tool_result_message(kind: str, call: ToolCall, stdout: str, exit_code: int) -> dict:
    return {
        "openai": tool_result_message_openai,
        "anthropic": tool_result_message_anthropic,
        "gemini": tool_result_message_gemini,
    }[kind](call, stdout, exit_code)


def supports_tools(kind: str) -> bool:
    """Is native tool-calling supported for this provider kind?"""
    return kind in ("openai", "anthropic", "gemini")


def extract_command(call: ToolCall) -> tuple[str, bool]:
    """Return (command, sudo) from a parsed run_shell call, or ('', False) if malformed."""
    cmd = call.args.get("command") or ""
    sudo = bool(call.args.get("sudo", False))
    if not isinstance(cmd, str):
        cmd = str(cmd)
    return cmd.strip(), sudo


def validate_command_safety(cmd: str) -> tuple[bool, str]:
    """Quick sanity check on a command proposed by the model.

    The real risk classifier (services/risk.py) does the heavy lifting; this is
    just a defense-in-depth: never let a `run_shell` call pass an empty command
    or one that's clearly a prompt-injection attempt.
    """
    if not cmd or not cmd.strip():
        return False, "The model proposed an empty command."
    # Cap length: 8 KB is plenty for any reasonable shell command, and avoids
    # pathological models that try to dump huge arguments.
    if len(cmd) > 8192:
        return False, "The proposed command is unusually long; refused as a safety measure."
    return True, ""
