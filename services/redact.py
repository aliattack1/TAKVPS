"""Secret redaction applied to everything sent to an AI provider."""
from __future__ import annotations

import re

_KEYS = (r"PASSWORD|PASSWD|PASSPHRASE|[_-]PASS|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|"
         r"AUTHORIZATION|CREDENTIALS?")
R = "[REDACTED]"
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*\Z", re.S), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED AWS KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED API KEY]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED SLACK TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED JWT]"),
    (re.compile(r"(\bbearer\s+)[A-Za-z0-9._~+/=-]{12,}", re.I), r"\1" + R),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@", re.I), r"\1" + R + "@"),
    (re.compile(r"(\bsshpass\s+-p\s*)\S+", re.I), r"\1" + R),
    (re.compile(r"(\bmysql\w*\s[^\n|;&]*?\s-p)\S+", re.I), r"\1" + R),
    (re.compile(r"""\b([A-Z0-9_.-]*(?:%s)[A-Z0-9_.-]*)(["']?\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s"',;]+)""" % _KEYS,
                re.I | re.M), r"\1\2" + R),
]


def redact(text: str) -> tuple[str, int]:
    """-> (clean text, number of replacements)."""
    total = 0
    for pat, repl in _PATTERNS:
        text, n = pat.subn(repl, text)
        total += n
    return text, total
