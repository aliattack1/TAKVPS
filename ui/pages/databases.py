"""Database manager over SSH: browse, query, dump/restore. Uses the server's own CLI tools; no DB ports are exposed.

Passwords are encrypted with the app vault and sent on stdin to a `read` wrapper, never in a command line.
"""
from __future__ import annotations

import csv
import io
import posixpath
import shlex
import time
from typing import Optional

import flet as ft

from core.errors import TakError
from core.fmt import fmt_mtime, human_size
from services import dbs as D
from services.backups import is_backup_file, slug
from services.runner import Step
from services.transfers import unique_local_path
from storage.settings import resolve_download_dir
from .. import theme
from ..components.common import confirm, toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext

MAX_RENDER_ROWS = 200
TABS = [("browse", "Browse"), ("query", "Query"), ("dumps", "Dumps")]


def result_grid(res: D.Result) -> ft.Control:
    if res.error:
        return ft.Text(res.error, color=ft.Colors.ERROR, font_family="monospace", size=11, selectable=True)
    if not res.columns:
        return ft.Text(res.text, font_family="monospace", size=11, selectable=True)
    table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(c, size=11, weight=ft.FontWeight.W_700)) for c in res.columns], column_spacing=18,
        rows=[ft.DataRow(cells=[ft.DataCell(ft.Text((v if len(v) <= 60 else v[:59] + "…"), size=11)) for v in r[:len(res.columns)]])
              for r in res.rows[:MAX_RENDER_ROWS]])
    more = [ft.Text(f"Showing {MAX_RENDER_ROWS} of {len(res.rows)} rows.", size=11, opacity=0.6)] if len(res.rows) > MAX_RENDER_ROWS else []
    return ft.Column([ft.Row([table], scroll=ft.ScrollMode.AUTO), *more], spacing=4)


def to_csv(res: D.Result) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(res.columns)
    w.writerows(res.rows)
    return buf.getvalue()


class DatabasesView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.engines: Optional[list[str]] = None
        self.engine, self.auth = "", D.Auth()
        self.mode = "browse"
        self.db = self.table = ""
        self.page_no = 0
        self.total: Optional[str] = None
        self.names: list[str] = []
        self.result: Optional[D.Result] = None
        self.redis_db = "0"
        self.msg = "Checking this server…"
        self.progress = ft.ProgressBar(visible=False)
        self.engine_dd = ft.Dropdown(label="Engine", expand=True, on_select=self._on_engine)
        self.seg = ft.SegmentedButton(selected=["browse"], on_change=self._on_mode,
                                      segments=[ft.Segment(value=k, label=ft.Text(v)) for k, v in TABS])
        self.sql = ft.TextField(label="Query / command", multiline=True, min_lines=3, max_lines=8,
                                text_style=ft.TextStyle(font_family="monospace", size=12))
        self.body = ft.Column(spacing=8)
        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
            ft.Row([ft.Text("Databases", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, width=44, height=44, tooltip="Refresh", on_click=lambda _: self.page.run_task(self.refresh))], scroll=ft.ScrollMode.AUTO),
            ft.Row([self.engine_dd, ft.FilledTonalButton("Connection…", icon=ft.Icons.KEY, on_click=lambda _: self.open_auth())]),
            self.seg, self.progress, self.body])
        self.render()

    @property
    def cid(self) -> str:
        return self.ctx.active.id if self.ctx.active else ""

    # ---- plumbing ----
    async def run(self, c: D.Cmd, timeout: float = 60, log_as: str = ""):
        label = log_as or f"database {self.engine} query"          # query text can hold data: never written to the log
        if c.privileged:
            return await self.ctx.run_privileged(c.cmd, timeout, input_text=c.stdin, log_as=label)
        return await self.ctx.ssh.run(c.cmd, timeout=timeout, input_text=c.stdin)

    async def q(self, sql: str, db: str = "", limit: int = 501) -> D.Result:
        try:
            r = await self.run(D.build(self.engine, self.auth, sql, db, limit_lines=limit))
        except ValueError as e:
            return D.Result(error=str(e))
        except TakError as e:
            return D.Result(error=e.message)
        return D.Result(error="Cancelled.") if r is None else D.parse_result(self.engine, r.stdout + r.stderr)

    async def qr(self, args: list[str], limit: int = 301) -> D.Result:
        try:
            r = await self.run(D.build("redis", self.auth, db=self.redis_db, redis_args=args, limit_lines=limit))
        except (ValueError, TakError) as e:
            return D.Result(error=getattr(e, "message", None) or str(e))
        return D.Result(error="Cancelled.") if r is None else D.Result(text=(r.stdout + r.stderr).strip() or "(empty)")

    async def load(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.msg = "Not connected."
            self.render()
            return
        try:
            self.engines = D.detect((await self.ctx.ssh.run(D.DETECT_CMD, timeout=15)).stdout)
        except TakError as e:
            self.msg = e.message
            self.render()
            return
        if not self.engines:
            self.msg = "No database client tools found on this server (mysql/mariadb, psql, sqlite3, redis-cli)."
        else:
            self.engine_dd.options = [ft.DropdownOption(key=e, text=D.ENGINES[e]) for e in self.engines]
            await self._select(self.engines[0])
            self.engine_dd.value = self.engine
        self.render()

    async def _select(self, engine: str) -> None:
        if self.ctx.db_profiles.has_password(self.cid, engine):
            await self.ctx.ensure_unlocked()
        self.engine = engine
        self.auth = self.ctx.db_profiles.get(self.cid, engine, self.ctx.vault)
        self.db = self.table = ""
        self.names, self.result, self.page_no = [], None, 0
        if engine == "redis" and self.auth.mode != "password":
            self.auth.mode = "socket"
        await self.refresh()

    def _on_engine(self, e) -> None:
        self.page.run_task(self._select, e.control.value)

    def _on_mode(self, e) -> None:
        self.mode = next(iter(e.control.selected))
        self.page.run_task(self.refresh)

    # ---- browse ----
    async def refresh(self) -> None:
        if not self.engine:
            self.render()
            return
        if self.mode == "browse":
            self.progress.visible = True
            self.render()
            await self._browse()
            self.progress.visible = False
        elif self.mode == "dumps":
            await self._list_dumps()
        self.render()

    async def _browse(self) -> None:
        e = self.engine
        self.result = None
        if e == "redis":
            r = await self.qr(["--scan", "--count", "200"], 200)
            self.names = [] if r.error else [l for l in r.text.splitlines() if l and l != "(empty)"]
            if r.error:
                self.result = r
            return
        if e == "sqlite" and not self.auth.path:
            self.names, self.result = [], D.Result(text="Enter the SQLite file path under “Connection…”.")
            return
        if self.table:
            res = await self.q(D.sql_rows(e, self.db, self.table, self.page_no), self.db if e != "sqlite" else "")
            self.result = res
            if self.page_no == 0 and not res.error:
                c = await self.q(D.sql_count(e, self.db, self.table), self.db if e != "sqlite" else "", 5)
                self.total = c.rows[0][0] if c.rows and c.rows[0] else None
            return
        sql = D.sql_tables(e, self.db) if (self.db or e == "sqlite") else D.sql_databases(e)
        res = await self.q(sql, self.db if e == "postgres" else "")
        self.names = [r[0] for r in res.rows if r] if not res.error else []
        self.result = res if res.error else None

    def _crumbs(self) -> ft.Control:
        parts = [ft.TextButton(D.ENGINES.get(self.engine, "…"), on_click=lambda _: self._go("", ""))]
        if self.db:
            parts.append(ft.TextButton(self.db, on_click=lambda _: self._go(self.db, "")))
        if self.table:
            parts.append(ft.Text(self.table, weight=ft.FontWeight.W_600))
        return ft.Row(parts, scroll=ft.ScrollMode.AUTO, spacing=0)

    def _go(self, db: str, table: str) -> None:
        self.db, self.table, self.page_no, self.total = db, table, 0, None
        self.page.run_task(self.refresh)

    def _browse_ui(self) -> list[ft.Control]:
        e = self.engine
        out: list[ft.Control] = [self._crumbs()]
        if e == "redis":
            out.append(ft.TextField(label="Redis database number", value=self.redis_db, width=200, keyboard_type=ft.KeyboardType.NUMBER,
                                    on_submit=lambda ev: self._set_redis_db(ev.control.value)))
            if self.result and self.result.error:
                return out + [result_grid(self.result)]
            out += [ft.ListTile(title=ft.Text(k, no_wrap=True), dense=True, leading=ft.Icon(ft.Icons.KEY, size=18),
                                on_click=lambda _, k=k: self.page.run_task(self.redis_key, k)) for k in self.names[:200]]
            return out + ([] if self.names else [ft.Text("No keys found.", opacity=0.7)])
        if self.table:
            res = self.result or D.Result(text="…")
            out.append(ft.Row([ft.Text(f"page {self.page_no + 1}" + (f" · {self.total} rows" if self.total else ""), size=12, expand=True),
                               ft.IconButton(ft.Icons.CHEVRON_LEFT, width=40, height=40, disabled=self.page_no == 0, on_click=lambda _: self._page(-1)),
                               ft.IconButton(ft.Icons.CHEVRON_RIGHT, width=40, height=40, disabled=len(res.rows) < D.PAGE, on_click=lambda _: self._page(1)),
                               ft.TextButton("Structure", on_click=lambda _: self.page.run_task(self.structure)),
                               ft.TextButton("Copy CSV", on_click=lambda _: self.page.run_task(self.ctx.terminal.copy_text, to_csv(res)))],
                              wrap=True, spacing=0))
            return out + [result_grid(res)]
        if self.result and self.result.error:
            return out + [result_grid(self.result)]
        if self.result and not self.names:
            return out + [ft.Text(self.result.text, opacity=0.7)]
        icon = ft.Icons.STORAGE if not self.db and e != "sqlite" else ft.Icons.TABLE_CHART
        out += [ft.ListTile(title=ft.Text(n, no_wrap=True), dense=True, leading=ft.Icon(icon, size=18),
                            on_click=lambda _, n=n: self._open(n)) for n in self.names]
        return out + ([] if self.names else [ft.Text("Nothing here.", opacity=0.7)])

    def _open(self, name: str) -> None:
        if self.engine == "sqlite" or self.db:
            self._go(self.db, name)
        else:
            self._go(name, "")

    def _page(self, d: int) -> None:
        self.page_no = max(0, self.page_no + d)
        self.page.run_task(self.refresh)

    def _set_redis_db(self, v: str) -> None:
        self.redis_db = v if v.isdigit() else "0"
        self.page.run_task(self.refresh)

    async def structure(self) -> None:
        res = await self.q(D.sql_describe(self.engine, self.db, self.table), self.db if self.engine != "sqlite" else "")
        self.page.show_dialog(ft.AlertDialog(title=ft.Text(self.table), scrollable=True,
                                             content=ft.Container(width=min(380, (page.width or 400) - 32), content=result_grid(res)),
                                             actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))

    async def redis_key(self, key: str) -> None:
        t = await self.qr(["TYPE", key], 5)
        r = await self.qr(D.redis_inspect_args(key, t.text.strip()), 100)
        self.page.show_dialog(ft.AlertDialog(title=ft.Text(key, no_wrap=True), scrollable=True,
                                             content=ft.Container(width=min(380, (page.width or 400) - 32), content=ft.Column([
                                                 ft.Text(f"type: {t.text.strip()}", size=11, opacity=0.7), result_grid(r)], tight=True)),
                                             actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))

    # ---- query ----
    async def run_query(self) -> None:
        text = (self.sql.value or "").strip()
        if not text:
            return
        if self.engine == "redis":
            try:
                args = shlex.split(text)
            except ValueError:
                toast(self.page, "Unbalanced quotes in the command.", error=True)
                return
            kind = D.redis_kind(args)
        else:
            kind = D.sql_kind(text)
        if kind == "blocked":
            self.result = D.Result(error="Blocked: this can read or write files on the server, run programs, or change server "
                                         "configuration. Use the terminal if you really need it.")
            self.render()
            return
        if kind == "destructive" and not await self.ctx.ask_typed("Destructive statement", f"{text[:600]}\n\nThis can permanently "
                                                                  "delete or change data. Consider a dump first.", "RUN"):
            return
        if kind == "write" and not await self.ctx.ask_yes_no("This changes data", text[:600], "Run"):
            return
        if kind != "read" and not await self.ctx.safe_confirm("Run a data-changing statement", text[:300]):
            return
        self.progress.visible = True
        self.render()
        self.result = await (self.qr(args, 301) if self.engine == "redis" else self.q(text, self.db if self.engine == "postgres" else self.db,
                                                                                    501 if kind == "read" else 0))
        self.progress.visible = False
        self.render()

    # ---- dumps ----
    @property
    def dump_dir(self) -> str:
        return posixpath.join(self.ctx.ssh.home if self.ctx.ssh.status.value == "connected" else "/root", "takvps-backups", "db")

    async def _list_dumps(self) -> None:
        try:
            self.dumps = [e for e in await self.ctx.ssh.listdir(self.dump_dir) if is_backup_file(e.name) or e.name.endswith(".sql.gz")]
        except TakError:
            self.dumps = []
        if self.engine in ("mysql", "postgres") and not self.names:
            res = await self.q(D.sql_databases(self.engine))
            self.names = [r[0] for r in res.rows if r] if not res.error else []

    async def make_dump(self, db: str) -> None:
        if not db and self.engine != "sqlite":
            toast(self.page, "Choose a database first.", error=True)
            return
        dest = posixpath.join(self.dump_dir, f"{slug(db or posixpath.basename(self.auth.path))}-{time.strftime('%Y%m%d-%H%M%S')}.sql.gz")
        try:
            c = D.dump_command(self.engine, self.auth, db, dest)
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        if not await self.ctx.ask_yes_no("Create a dump?", f"{db or self.auth.path}\n→ {dest}\n\nThe file contains your data; it is created "
                                         "readable by its owner only.", "Create"):
            return
        rep = await run_in_sheet(self.ctx, f"Dump {db}", [Step("dump", c.cmd, sudo=c.privileged, stdin=c.stdin)],
                                 {"kind": "database", "name": f"dump {db}", "command": f"database dump ({self.engine})", "risk": "writes"})
        if rep is not None:
            await self.refresh()

    async def restore(self, e, db: str) -> None:
        if not db and self.engine != "sqlite":
            toast(self.page, "Choose the target database first.", error=True)
            return
        if not await self.ctx.ask_typed("Restore a dump?", f"This loads {e.name} into “{db or self.auth.path}” and can overwrite or "
                                        "duplicate existing data. Make a fresh dump first if you might need to go back.", "RESTORE"):
            return
        try:
            c = D.restore_command(self.engine, self.auth, db, e.path)
        except ValueError as ex:
            toast(self.page, str(ex), error=True)
            return
        await run_in_sheet(self.ctx, f"Restore {db}", [Step("restore", c.cmd, sudo=c.privileged, stdin=c.stdin)],
                           {"kind": "database", "name": f"restore {db}", "command": f"database restore ({self.engine})", "risk": "destructive"})

    def _dumps_ui(self) -> list[ft.Control]:
        if self.engine == "redis":
            return [ft.Text("Redis isn't dumped here. Copy dump.rdb from its data directory (see the Files page).", opacity=0.7)]
        target = ft.Dropdown(label="Database", value=self.db or None, options=[ft.DropdownOption(key=n) for n in self.names],
                             visible=self.engine != "sqlite", on_select=lambda ev: setattr(self, "db", ev.control.value))
        out: list[ft.Control] = [target, ft.FilledButton("Create dump", icon=ft.Icons.BACKUP,
                                                           on_click=lambda _: self.page.run_task(self.make_dump, self.db))]
        out.append(ft.Text(f"Dumps in {self.dump_dir}", size=12, opacity=0.7))
        for e in sorted(getattr(self, "dumps", []), key=lambda x: -x.mtime)[:30]:
            out.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Row([ft.Icon(ft.Icons.ARCHIVE), ft.Column([ft.Text(e.name, size=12, weight=ft.FontWeight.W_600, no_wrap=True),
                                ft.Text(f"{human_size(e.size)} · {fmt_mtime(e.mtime)}", size=11, opacity=0.7)], spacing=0, expand=True),
                                ft.IconButton(ft.Icons.DOWNLOAD, width=40, height=40, tooltip="Download", on_click=lambda _, e=e: self._download(e)),
                                ft.IconButton(ft.Icons.RESTORE, width=40, height=40, tooltip="Restore…",
                                              on_click=lambda _, e=e: self.page.run_task(self.restore, e, self.db)),
                                ft.IconButton(ft.Icons.DELETE, width=40, height=40, tooltip="Delete", on_click=lambda _, e=e: self._delete(e))],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        return out

    def _download(self, e) -> None:
        dest = resolve_download_dir(self.ctx.store.data)
        self.ctx.transfers.add_download(e.path, str(unique_local_path(dest, e.name)), e.name, e.size)
        toast(self.page, f"Queued download → {dest}")

    def _delete(self, e) -> None:
        async def do():
            try:
                await self.ctx.ssh.remove(e.path)
                self.ctx.log_action(f"rm {shlex.quote(e.path)}", 0)
            except TakError as ex:
                toast(self.page, ex.message, error=True)
            await self.refresh()
        confirm(self.page, "Delete this dump?", e.name, do, yes_label="Delete")

    # ---- connection dialog ----
    def open_auth(self) -> None:
        if not self.engine:
            return
        page, a, eng = self.page, self.auth, self.engine
        has_pw = self.ctx.db_profiles.has_password(self.cid, eng)
        mode = ft.Dropdown(label="Sign in", value=a.mode, options=[
            ft.DropdownOption(key="socket", text={"mysql": "As root on the server (socket, uses sudo)", "postgres": "As the postgres user (uses sudo)"}.get(eng, "Local / no password")),
            ft.DropdownOption(key="password", text="With a user and password")], visible=eng in ("mysql", "postgres", "redis"))
        user = ft.TextField(label="User", value=a.user, visible=eng in ("mysql", "postgres"))
        pw = ft.TextField(label="Password", password=True, can_reveal_password=True,
                          hint_text="Leave blank to keep the saved one" if has_pw else "", visible=eng in ("mysql", "postgres", "redis"))
        host = ft.TextField(label="Host (as the server sees it)", value=a.host or "127.0.0.1", visible=eng != "sqlite")
        port = ft.TextField(label="Port (blank = default)", value=str(a.port or ""), keyboard_type=ft.KeyboardType.NUMBER, visible=eng != "sqlite")
        path = ft.TextField(label="SQLite file (absolute path)", value=a.path, visible=eng == "sqlite")
        sudo = ft.Switch(label="Read as administrator (sudo)", value=a.sudo, visible=eng in ("sqlite", "redis"))

        async def save(_):
            try:
                auth = D.Auth(mode=mode.value or "socket", user=(user.value or "").strip(), host=(host.value or "127.0.0.1").strip(),
                              port=int(port.value) if (port.value or "").strip() else 0, path=(path.value or "").strip(), sudo=sudo.value)
                if auth.mode == "password" and eng in ("mysql", "postgres") and not auth.user:
                    raise ValueError("Enter the database user.")
            except ValueError as e:
                toast(page, str(e) if "user" in str(e) else "Port must be a number.", error=True)
                return
            secret = None if not (pw.value or "") else pw.value
            if secret and not await self.ctx.ensure_unlocked():
                return
            if eng == "redis":
                auth.mode = "password" if (secret or has_pw) else "socket"
            self.ctx.db_profiles.save(self.cid, eng, auth, self.ctx.vault, secret)
            self.auth = self.ctx.db_profiles.get(self.cid, eng, self.ctx.vault)
            page.pop_dialog()
            await self.refresh()
        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(f"{D.ENGINES[eng]} connection"), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([mode, user, pw, host, port, path, sudo,
                ft.Text("The password is stored encrypted on this phone and sent to the server on stdin — never in a command line.",
                        size=11, opacity=0.7)], tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()), ft.FilledButton("Save", on_click=save)]))

    # ---- rendering ----
    def render(self) -> None:
        if self.engines is None or not self.engine:
            self.body.controls = [ft.Container(padding=24, content=ft.Text(self.msg, opacity=0.7))]
        elif self.mode == "browse":
            self.body.controls = self._browse_ui()
        elif self.mode == "query":
            hint = "Redis command, e.g. GET mykey" if self.engine == "redis" else "SQL (read-only queries run straight away; changes ask first)"
            self.sql.label = hint
            self.body.controls = [self.sql, ft.Row([ft.FilledButton("Run", icon=ft.Icons.PLAY_ARROW, on_click=lambda _: self.page.run_task(self.run_query)),
                                                    ft.Text(f"on {self.db}" if self.db else "", size=11, opacity=0.7)], scroll=ft.ScrollMode.AUTO),
                                  result_grid(self.result) if self.result else ft.Text("Results appear here.", opacity=0.6)]
        else:
            self.body.controls = self._dumps_ui()
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass


def databases_page(ctx: AppContext) -> ft.Control:
    v = DatabasesView(ctx)
    ctx.page.run_task(v.load)
    return v.control
