"""Database helpers (MySQL/MariaDB, PostgreSQL, SQLite, Redis) via the server's own CLI tools. Pure.

Passwords never appear in a command line: they are fed on stdin to a tiny `sh -c 'read P; export …'` wrapper.
"""
from __future__ import annotations

import csv
import io
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

ENGINES = {"mysql": "MySQL / MariaDB", "postgres": "PostgreSQL", "sqlite": "SQLite", "redis": "Redis"}
DETECT_CMD = "for t in mysql mariadb psql redis-cli sqlite3; do command -v $t >/dev/null 2>&1 && echo $t; done; true"
PAGE = 50
_IDENT = re.compile(r"^[A-Za-z0-9_$.-]{1,128}$")


def detect(text: str) -> list[str]:
    t = set(text.split())
    out = []
    if t & {"mysql", "mariadb"}:
        out.append("mysql")
    if "psql" in t:
        out.append("postgres")
    if "sqlite3" in t:
        out.append("sqlite")
    if "redis-cli" in t:
        out.append("redis")
    return out


@dataclass
class Auth:
    mode: str = "socket"                 # socket (root/postgres via sudo) | password
    user: str = ""
    password: str = field(default="", repr=False)
    host: str = "127.0.0.1"
    port: int = 0
    path: str = ""                       # SQLite file
    sudo: bool = False                   # sqlite / redis: run as root


@dataclass
class Cmd:
    cmd: str
    stdin: Optional[str] = None
    privileged: bool = False


def ident_ok(n: str) -> bool:
    return bool(_IDENT.match(n))


def _need_ident(n: str) -> str:
    if not ident_ok(n):
        raise ValueError(f"“{n}” isn't a plain database/table name (letters, digits, _ $ . - only).")
    return n


def qmy(n: str) -> str:
    return "`" + _need_ident(n) + "`"


def qpg(n: str) -> str:
    return ".".join('"' + _need_ident(p) + '"' for p in n.split(".", 1)) if "." in n else '"' + _need_ident(n) + '"'


def _host_ok(h: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9.:_-]{1,253}", h) or h.startswith("-"):
        raise ValueError("Invalid host.")
    return h


def _wrap_password(script_env: str, tool_line: str, auth: Auth, pipeline: str = "") -> Cmd:
    body = f'IFS= read -r P; export {script_env}="$P"; {pipeline or "exec " + tool_line}'
    return Cmd("sh -c " + shlex.quote(body), auth.password + "\n", False)


# ------------------------------------------------------------------ building commands
def _mysql_args(auth: Auth) -> str:
    a = ""
    if auth.mode == "password":
        a += f" -u {shlex.quote(auth.user)} -h {shlex.quote(_host_ok(auth.host))}" + (f" -P {int(auth.port)}" if auth.port else "")
    return a


MYSQL_BIN = "$(command -v mysql || command -v mariadb)"
MYSQLDUMP_BIN = "$(command -v mysqldump || command -v mariadb-dump)"


def build(engine: str, auth: Auth, sql: str = "", db: str = "", redis_args: Optional[list[str]] = None,
          limit_lines: int = 0) -> Cmd:
    tail = f" 2>&1 | head -n {int(limit_lines)}" if limit_lines else " 2>&1"
    if engine == "mysql":
        line = f"{MYSQL_BIN} --batch{_mysql_args(auth)}" + (f" {shlex.quote(_need_ident(db))}" if db else "") + f" -e {shlex.quote(sql)}"
        if auth.mode == "password":
            c = _wrap_password("MYSQL_PWD", line, auth, pipeline=line + tail)
            return c
        return Cmd(line + tail, None, True)
    if engine == "postgres":
        flags = "-X --csv -v ON_ERROR_STOP=1" + (f" -d {shlex.quote(_need_ident(db))}" if db else "") + f" -c {shlex.quote(sql)}"
        if auth.mode == "password":
            host = f" -h {shlex.quote(_host_ok(auth.host))}" + (f" -p {int(auth.port)}" if auth.port else "")
            line = f"psql{host} -U {shlex.quote(auth.user)} {flags}"
            return _wrap_password("PGPASSWORD", line, auth, pipeline=line + tail)
        return Cmd(f"runuser -u postgres -- psql {flags}{tail}", None, True)
    if engine == "sqlite":
        if not auth.path.startswith("/") or "\n" in auth.path:
            raise ValueError("Enter the absolute path of the SQLite file.")
        ro = "" if sql_kind(sql) != "read" else " -readonly"
        return Cmd(f"sqlite3 -csv -header{ro} {shlex.quote(auth.path)} {shlex.quote(sql)}{tail}", None, auth.sudo)
    if engine == "redis":
        base = "redis-cli --no-auth-warning" + f" -h {shlex.quote(_host_ok(auth.host))}" + (f" -p {int(auth.port)}" if auth.port else "")
        if db:
            base += f" -n {int(db)}"
        args = " ".join(shlex.quote(a) for a in (redis_args or []))
        line = f"{base} {args}"
        if auth.mode == "password" and auth.password:
            return _wrap_password("REDISCLI_AUTH", line, auth, pipeline=line + tail)
        return Cmd(line + tail, None, auth.sudo)
    raise ValueError("Unknown database engine.")


def dump_command(engine: str, auth: Auth, db: str, dest: str) -> Cmd:
    if not dest.startswith("/") or "\n" in dest or not dest.endswith(".gz"):
        raise ValueError("The dump file must be an absolute path ending in .gz")
    d, f = shlex.quote(posixpath.dirname(dest)), shlex.quote(dest)
    prep = f"umask 077; mkdir -p {d} && "                   # dumps contain data: owner-only permissions
    if engine == "mysql":
        line = f"{MYSQLDUMP_BIN} --single-transaction --routines --triggers{_mysql_args(auth)} {shlex.quote(_need_ident(db))} | gzip > {f}"
        if auth.mode == "password":
            return _wrap_password("MYSQL_PWD", line, auth, pipeline=prep + line)
        return Cmd(prep + line, None, True)
    if engine == "postgres":
        if auth.mode == "password":
            host = f" -h {shlex.quote(_host_ok(auth.host))}" + (f" -p {int(auth.port)}" if auth.port else "")
            line = f"pg_dump{host} -U {shlex.quote(auth.user)} {shlex.quote(_need_ident(db))} | gzip > {f}"
            return _wrap_password("PGPASSWORD", line, auth, pipeline=prep + line)
        return Cmd(prep + f"runuser -u postgres -- pg_dump {shlex.quote(_need_ident(db))} | gzip > {f}", None, True)
    if engine == "sqlite":
        return Cmd(prep + f"sqlite3 {shlex.quote(auth.path)} .dump | gzip > {f}", None, auth.sudo)
    raise ValueError("Dumps aren't supported for this engine here (for Redis, copy dump.rdb).")


def restore_command(engine: str, auth: Auth, db: str, src: str) -> Cmd:
    if not src.startswith("/") or "\n" in src:
        raise ValueError("The dump file must be an absolute path.")
    feed = f"gunzip -c {shlex.quote(src)}" if src.endswith(".gz") else f"cat {shlex.quote(src)}"
    if engine == "mysql":
        line = f"{feed} | {MYSQL_BIN}{_mysql_args(auth)} {shlex.quote(_need_ident(db))}"
        return _wrap_password("MYSQL_PWD", line, auth, pipeline=line) if auth.mode == "password" else Cmd(line, None, True)
    if engine == "postgres":
        if auth.mode == "password":
            host = f" -h {shlex.quote(_host_ok(auth.host))}" + (f" -p {int(auth.port)}" if auth.port else "")
            line = f"{feed} | psql -X -v ON_ERROR_STOP=1{host} -U {shlex.quote(auth.user)} -d {shlex.quote(_need_ident(db))}"
            return _wrap_password("PGPASSWORD", line, auth, pipeline=line)
        return Cmd(f"{feed} | runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d {shlex.quote(_need_ident(db))}", None, True)
    if engine == "sqlite":
        return Cmd(f"{feed} | sqlite3 {shlex.quote(auth.path)}", None, auth.sudo)
    raise ValueError("Restore isn't supported for this engine here.")


# ------------------------------------------------------------------ browsing SQL
def sql_databases(engine: str) -> str:
    return {"mysql": "SHOW DATABASES",
            "postgres": "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1"}[engine]


def sql_tables(engine: str, db: str) -> str:
    if engine == "mysql":
        return f"SHOW TABLES FROM {qmy(db)}"
    if engine == "postgres":
        return ("SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                "ORDER BY 1")
    return "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"


def sql_rows(engine: str, db: str, table: str, page: int, size: int = PAGE) -> str:
    off = max(0, int(page)) * size
    if engine == "mysql":
        tbl = f"{qmy(db)}.{qmy(table)}"
    elif engine == "postgres":
        tbl = qpg(table)
    else:
        tbl = '"' + _need_ident(table) + '"'
    return f"SELECT * FROM {tbl} LIMIT {size} OFFSET {off}"


def sql_count(engine: str, db: str, table: str) -> str:
    if engine == "mysql":
        t = qmy(db) + "." + qmy(table)
    elif engine == "postgres":
        t = qpg(table)
    else:
        t = '"' + _need_ident(table) + '"'
    return f"SELECT COUNT(*) FROM {t}"


def sql_describe(engine: str, db: str, table: str) -> str:
    if engine == "mysql":
        return f"DESCRIBE {qmy(db)}.{qmy(table)}"
    if engine == "postgres":
        sch, _, t = table.partition(".")
        return ("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema="
                f"'{_need_ident(sch)}' AND table_name='{_need_ident(t)}' ORDER BY ordinal_position")
    return f'PRAGMA table_info("{_need_ident(table)}")'


# ------------------------------------------------------------------ results
@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    error: str = ""
    text: str = ""


_ERR = re.compile(r"^(ERROR\b|psql: error|Error: |sqlite3?: |bash: |sh: |mysql: \[?ERROR|ERROR:)", re.I)


def _unescape(s: str) -> str:
    return re.sub(r"\\([tn\\0])", lambda m: {"t": "\t", "n": "\n", "\\": "\\", "0": "\0"}[m.group(1)], s)


def parse_result(engine: str, text: str) -> Result:
    t = text.strip("\n")
    if not t.strip():
        return Result(text="(no output)")
    first = t.splitlines()[0]
    if _ERR.match(first) or "command not found" in first:
        return Result(error=t[:1500])
    if engine == "mysql":
        lines = t.splitlines()
        if len(lines) > 1 or "\t" in lines[0]:
            return Result(columns=lines[0].split("\t"), rows=[[_unescape(c) for c in ln.split("\t")] for ln in lines[1:]], text=t)
        return Result(text=t)
    if engine in ("postgres", "sqlite"):
        try:
            rows = list(csv.reader(io.StringIO(t)))
        except csv.Error:
            return Result(text=t)
        if len(rows) >= 1 and len(set(len(r) for r in rows)) <= 2 and not re.match(r"^(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER) ", first):
            return Result(columns=rows[0], rows=rows[1:], text=t)
    return Result(text=t)


# ------------------------------------------------------------------ safety classification
_FORBIDDEN = re.compile(r"\binto\s+(outfile|dumpfile)\b|\bload_file\s*\(|\bload\s+data\b|\bcopy\b.*\b(program|to\s+'/)|"
                        r"\bpg_read_file\b|\bpg_ls_dir\b|\blo_(import|export)\b|\bxp_cmdshell\b|\battach\s+database\b", re.I | re.S)
_DESTRUCTIVE = {"drop", "truncate", "delete", "alter", "grant", "revoke", "shutdown", "kill", "detach"}
_WRITE = {"insert", "update", "create", "replace", "rename", "merge", "call", "exec", "vacuum", "reindex", "set", "use_write",
          "lock", "unlock", "begin", "commit", "rollback", "analyze", "optimize", "repair", "flush", "reset", "load"}
_READ = {"select", "show", "describe", "desc", "explain", "pragma", "values", "table", "use", "with"}


def split_statements(sql: str) -> list[str]:
    out, cur, q = [], [], ""
    i = 0
    while i < len(sql):
        c = sql[i]
        if q:
            cur.append(c)
            if c == "\\" and q != "`" and i + 1 < len(sql):
                cur.append(sql[i + 1])
                i += 1
            elif c == q:
                q = ""
        elif c in "'\"`":
            q = c
            cur.append(c)
        elif c == "-" and sql[i:i + 2] == "--":
            j = sql.find("\n", i)
            i = len(sql) if j < 0 else j
            continue
        elif c == ";":
            s = "".join(cur).strip()
            if s:
                out.append(s)
            cur = []
        else:
            cur.append(c)
        i += 1
    s = "".join(cur).strip()
    if s:
        out.append(s)
    return out


def sql_kind(sql: str) -> str:
    """read | write | destructive | blocked (blocked = refuse; use the terminal)."""
    if sql.lstrip().startswith("\\") or _FORBIDDEN.search(sql):
        return "blocked"
    kind = "read"
    for st in split_statements(sql):
        words = re.findall(r"[A-Za-z_]+", st)
        first = words[0].lower() if words else ""
        lw = {w.lower() for w in words}
        if first in _DESTRUCTIVE:
            return "destructive"
        if first in ("update", "delete") and not re.search(r"\bwhere\b", st, re.I):
            return "destructive"
        if first == "with" and lw & {"insert", "update", "delete"}:
            kind = "write"
        elif first == "pragma" and "=" in st:
            kind = "write"
        elif first in _READ:
            continue
        else:
            kind = "write"
    return kind


_REDIS_READ = {"get", "mget", "type", "ttl", "pttl", "exists", "scan", "hget", "hgetall", "hkeys", "hlen", "lrange", "llen",
               "smembers", "scard", "sismember", "zrange", "zcard", "zscore", "strlen", "info", "dbsize", "ping", "memory", "object"}
_REDIS_BLOCKED = {"config", "eval", "evalsha", "script", "debug", "shutdown", "slaveof", "replicaof", "module", "migrate",
                  "acl", "monitor", "subscribe", "psubscribe", "sync", "psync", "client", "keys", "bgsave", "bgrewriteaof", "save"}
_REDIS_DESTRUCTIVE = {"del", "unlink", "flushdb", "flushall"}


def redis_kind(args: list[str]) -> str:
    if not args:
        return "blocked"
    c = args[0].lower()
    if c in _REDIS_BLOCKED:
        return "blocked"
    if c in _REDIS_DESTRUCTIVE:
        return "destructive"
    return "read" if c in _REDIS_READ else "write"


def redis_inspect_args(key: str, typ: str) -> list[str]:
    return {"string": ["GET", key], "list": ["LRANGE", key, "0", "99"], "set": ["SMEMBERS", key],
            "zset": ["ZRANGE", key, "0", "99", "WITHSCORES"], "hash": ["HGETALL", key]}.get(typ, ["TYPE", key])
