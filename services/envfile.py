"""`.env` file parsing/editing that preserves comments, order and untouched lines. Pure."""
from __future__ import annotations

import re
from dataclasses import dataclass

SECRET_RE = re.compile(r"(PASS|SECRET|TOKEN|KEY|CRED|PRIVATE|AUTH|DSN|SALT|SIGNATURE|COOKIE|CERT)", re.I)
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LINE = re.compile(r"^\s*(export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=(.*)$")
_SAFE = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")


@dataclass
class EnvLine:
    raw: str
    kind: str = "other"          # "var" | "other"
    key: str = ""
    value: str = ""
    export: bool = False


def valid_key(k: str) -> bool:
    return bool(_KEY.match(k))


def is_secret(key: str) -> bool:
    return bool(SECRET_RE.search(key))


def mask(value: str) -> str:
    return "(empty)" if not value else "•" * min(len(value), 10)


def _parse_value(rest: str) -> str:
    r = rest.strip()
    if r.startswith('"'):
        out, i = [], 1
        while i < len(r):
            c = r[i]
            if c == "\\" and i + 1 < len(r):
                nxt = r[i + 1]
                out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                break
            out.append(c)
            i += 1
        return "".join(out)
    if r.startswith("'"):
        end = r.find("'", 1)
        return r[1:end] if end > 0 else r[1:]
    return re.split(r"\s+#", r, maxsplit=1)[0].strip()


def parse_env(text: str) -> list[EnvLine]:
    out = []
    for raw in text.splitlines():
        m = _LINE.match(raw) if not raw.lstrip().startswith("#") else None
        out.append(EnvLine(raw, "var", m.group(2), _parse_value(m.group(3)), bool(m.group(1))) if m else EnvLine(raw))
    return out


def format_value(v: str) -> str:
    if v == "" or _SAFE.match(v):
        return v
    if "'" not in v and "\n" not in v:
        return f"'{v}'"
    esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("\n", "\\n")
    return f'"{esc}"'


def _render(key: str, value: str, export: bool) -> str:
    return ("export " if export else "") + f"{key}={format_value(value)}"


def set_var(lines: list[EnvLine], key: str, value: str) -> list[EnvLine]:
    if not valid_key(key):
        raise ValueError("Variable names may only contain letters, digits and underscores, and can't start with a digit.")
    out, done = [], False
    for l in lines:
        if l.kind == "var" and l.key == key and not done:
            out.append(EnvLine(_render(key, value, l.export), "var", key, value, l.export))
            done = True
        else:
            out.append(l)
    if not done:
        out.append(EnvLine(_render(key, value, False), "var", key, value))
    return out


def remove_var(lines: list[EnvLine], key: str) -> list[EnvLine]:
    return [l for l in lines if not (l.kind == "var" and l.key == key)]


def serialize(lines: list[EnvLine]) -> str:
    t = "\n".join(l.raw for l in lines)
    return t + "\n" if t and not t.endswith("\n") else t


def looks_like_env(name: str) -> bool:
    n = name.lower()
    return n == ".env" or n.startswith(".env.") or n.endswith(".env")
