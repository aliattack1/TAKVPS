"""Command palette: items and fuzzy ranking. Pure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Item:
    kind: str            # page | server | snippet | service | container | site | file
    title: str
    subtitle: str = ""
    key: str = ""        # payload (page key, id, path…)


KIND_ORDER = {"page": 0, "server": 1, "snippet": 2, "service": 3, "container": 4, "site": 5, "file": 6}


def fuzzy_score(query: str, text: str) -> Optional[int]:
    """Subsequence match; higher is better. None if `query` isn't a subsequence of `text`."""
    q, t = query.lower().strip(), text.lower()
    if not q:
        return 0
    pos, score, prev = 0, 0, -2
    for ch in q:
        i = t.find(ch, pos)
        if i < 0:
            return None
        score += 10
        if i == prev + 1:
            score += 15                                   # consecutive characters
        if i == 0 or t[i - 1] in " -_./:":
            score += 12                                   # start of a word
        prev, pos = i, i + 1
    if t.startswith(q):
        score += 40
    elif q in t:
        score += 20
    return score - min(len(t), 60) // 6                   # prefer shorter titles


def search(items: list[Item], query: str, limit: int = 30) -> list[Item]:
    q = query.strip()
    if not q:
        return sorted(items, key=lambda i: (KIND_ORDER.get(i.kind, 9), i.title.lower()))[:limit]
    scored = []
    for it in items:
        a = fuzzy_score(q, it.title)
        b = fuzzy_score(q, it.subtitle)
        s = max([x for x in ((a + 20) if a is not None else None, b) if x is not None], default=None)
        if s is not None:
            scored.append((-s, KIND_ORDER.get(it.kind, 9), it.title.lower(), it))
    scored.sort(key=lambda x: x[:3])
    return [x[3] for x in scored[:limit]]
