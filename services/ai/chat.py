"""Chat model helpers: messages, fenced-block parsing, prompt/context building. Pure."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from ipaddress import ip_address
from typing import Callable, Optional
from urllib.parse import urlparse

SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "terminal"}
MODES = {"suggest": "Suggest only", "ask": "Ask each time", "agent": "Agent (read-only auto)"}


@dataclass
class Msg:
    role: str                       # user | assistant | result
    text: str
    cmd: str = ""                   # for role == "result"
    exit: Optional[int] = None
    ai: bool = False                # command was AI-initiated
    ts: float = field(default_factory=time.time)
    # when the assistant used native tool-calling ,
    # this holds the structured tool calls so the UI can render them for inspection.
    # Each entry is a dict with keys: id, name, args (dict), and optionally result_text + result_exit.
    # Stored as a list of dicts (not ToolCall dataclasses) so it survives JSON serialization
    # in ChatStore. Empty for user messages and the structured-fallback (bash-block) path.
    tool_calls: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Msg":
        keys = {"role", "text", "cmd", "exit", "ai", "ts", "tool_calls"}
        return cls(**{k: v for k, v in d.items() if k in keys})


@dataclass
class Block:
    kind: str        # "text" | "code"
    lang: str = ""
    content: str = ""
    closed: bool = True

    @property
    def is_command(self) -> bool:
        return self.kind == "code" and self.lang.lower() in SHELL_LANGS and self.closed


_FENCE = re.compile(r"^```[ \t]*([\w+-]*)[ \t]*$")


def split_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    cur: Optional[Block] = None
    buf: list[str] = []
    for line in text.split("\n"):
        m = _FENCE.match(line.strip()) if line.lstrip().startswith("```") else None
        if cur is None:
            if m is not None:
                if buf:
                    blocks.append(Block("text", content="\n".join(buf)))
                    buf = []
                cur = Block("code", lang=m.group(1), closed=False)
            else:
                buf.append(line)
        else:
            if m is not None and m.group(1) == "":
                cur.content, cur.closed = "\n".join(buf).rstrip("\n"), True
                blocks.append(cur)
                cur, buf = None, []
            else:
                buf.append(line)
    if cur is not None:
        cur.content = "\n".join(buf)
        blocks.append(cur)
    elif buf:
        blocks.append(Block("text", content="\n".join(buf)))
    return [b for b in blocks if b.content.strip() or b.kind == "code"]


def first_command(text: str) -> Optional[str]:
    for b in split_blocks(text):
        if b.is_command and b.content.strip():
            return b.content.strip()
    return None


def estimate_tokens(s: str) -> int:
    return max(1, len(s) // 4) if s else 0


def clip(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    h = limit // 2
    return text[:h] + "\n… [output truncated] …\n" + text[-h:]


def result_text(m: Msg) -> str:
    return f"Output of `{m.cmd}` (exit code {m.exit}):\n```\n{clip(m.text)}\n```"


def to_api_messages(msgs: list[Msg], redact: Callable[[str], str]) -> list[dict]:
    """Alternating user/assistant list (results become user turns), starting with user, redacted."""
    out: list[dict] = []
    for m in msgs:
        role = "assistant" if m.role == "assistant" else "user"
        content = redact(result_text(m) if m.role == "result" else m.text)
        if not content.strip():
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def is_local_url(url: str) -> bool:
    host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith(".local") or host.endswith(".lan"):
        return bool(host)
    try:
        ip = ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


@dataclass
class ContextOptions:
    os: bool = True
    shell: bool = True
    cwd: bool = True
    pkg: bool = True
    term_lines: int = 0
    services: bool = False


def build_context(opts: ContextOptions, facts: dict, terminal_tail: str = "",
                  services: Optional[list[str]] = None) -> str:
    lines: list[str] = []
    if opts.os and facts.get("distro"):
        lines.append(f"OS: {facts['distro']} (kernel {facts.get('kernel', '?')}, {facts.get('arch', '?')})")
    if opts.shell:
        lines.append("Shell: POSIX sh/bash-compatible")
    if opts.cwd and facts.get("cwd"):
        lines.append(f"File-manager folder: {facts['cwd']}")
    if opts.pkg and facts.get("pkg"):
        lines.append(f"Package manager: {facts['pkg']}")
    if facts.get("sudo"):
        lines.append(f"Privileges: {facts['sudo']}")
    if opts.services and services:
        lines.append("Running services: " + ", ".join(services[:40]))
    if opts.term_lines and terminal_tail.strip():
        tail = "\n".join(terminal_tail.splitlines()[-opts.term_lines:])
        lines.append("Recent terminal output:\n```\n" + clip(tail, 4000) + "\n```")
    return "\n".join(lines)


_BASE = ("You are an assistant inside a mobile SSH/server-management app, helping the user administer a Linux "
         "server. Be concise: the user reads on a phone.\n"
         "To propose a shell command, put it alone in a fenced code block tagged bash. Prefer simple, "
         "non-interactive commands; mention any risk in one sentence. Never put secrets in commands and never "
         "pipe downloads into a shell.\n")
_MODE = {
    "suggest": "The user decides whether to run commands; you cannot run them.",
    "ask": ("You may run commands: when you need information or want an action done, reply with exactly ONE bash "
            "block. After the user approves it you will receive its output as the next message. Investigate with "
            "read-only commands before changing anything. When finished, answer without a bash block."),
}
_MODE["agent"] = _MODE["ask"]


def build_system(mode: str, context: str, server_note: str = "") -> str:
    """Build the system prompt for the AI assistant.

    `server_note` is the per-server custom instructions from
    the connection profile (e.g. "This is production — don't restart nginx
    during 09:00–17:00 UTC"). When present, it's prepended to the rest of the
    system prompt so the model treats it as a hard constraint.
    """
    out = _BASE
    if server_note.strip():
        out += "Notes about this server (follow these strictly):\n" + server_note.strip() + "\n"
    out += _MODE.get(mode, _MODE["suggest"])
    return out + ("\n\nServer context:\n" + context if context else "")
