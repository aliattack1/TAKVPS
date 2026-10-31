"""Part 11 — Conversation branching.

Lets the user fork a conversation at any assistant message: everything before
and including that message is kept, everything after is discarded. The user can
then explore a different path without losing the context they've built up.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

from typing import List, Optional

from services.ai.chat import Msg


def branch_from(msgs: List[Msg], index: int) -> List[Msg]:
    """Return a new list containing messages[0..index] (inclusive).

    `index` is the position of the message to branch FROM (the last message
    to keep). Everything after it is discarded.

    Raises IndexError if index is out of range.
    """
    if index < 0 or index >= len(msgs):
        raise IndexError(f"Branch index {index} is out of range (0..{len(msgs) - 1})")
    return list(msgs[:index + 1])


def find_branchable(msgs: List[Msg]) -> List[int]:
    """Indices of messages that are safe branch points (assistant messages
    that have at least one message after them).

    User messages and result messages aren't useful branch points — you
    branch from an AI reply to try a different follow-up.
    """
    out = []
    for i, m in enumerate(msgs):
        if m.role == "assistant" and i < len(msgs) - 1:
            out.append(i)
    return out


def branch_summary(msgs: List[Msg], index: int) -> str:
    """One-line description of what the branch will keep/discard.
    e.g. 'Keep 5 messages, discard 3'"""
    kept = index + 1
    discarded = len(msgs) - kept
    return f"Keep {kept} message{'s' if kept != 1 else ''}, discard {discarded}"


def can_branch(msgs: List[Msg], index: int) -> bool:
    """True if branching at `index` makes sense (there's something to discard
    and the message is an assistant reply)."""
    if not msgs or index < 0 or index >= len(msgs):
        return False
    if index >= len(msgs) - 1:
        return False     # nothing after this to discard
    return msgs[index].role == "assistant"
