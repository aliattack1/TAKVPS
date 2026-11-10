"""Backups helper: tar a folder on the server, optionally download it, optionally schedule it with cron."""
from __future__ import annotations

import posixpath
import shlex
import time

import flet as ft

from core.errors import TakError
from core.fmt import fmt_mtime, human_size
from services import backups as B
from services import cron as C
from services import files as F
from services.i18n import translate
from services.runner import Step
from services.snippets import new_id
from services.transfers import unique_local_path
from storage.settings import data_dir, resolve_download_dir
from .. import theme
from ..components.common import confirm, toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext

_FREE_CMD = ('d={d}; while [ ! -d "$d" ]; do d=$(dirname "$d"); done; df -Pk "$d" | tail -n 1')


class BackupsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        _t = self._t
        home = ctx.ssh.home if ctx.ssh.status.value == "connected" else "/root"
        self.src = ft.TextField(label=_t("Folder to back up"), value=ctx.files_state.path or "", hint_text="/var/www/mysite")
        self.name = ft.TextField(label=_t("Backup name"), value="", hint_text="mysite")
        self.dest = ft.TextField(label=_t("Save backups in (on the server)"), value=posixpath.join(home, "takvps-backups"))
        self.excl = ft.TextField(label=_t("Exclude (comma separated, optional)"), hint_text="*.log, node_modules, .cache")
        self.sudo = ft.Switch(label=_t("Read files as administrator (sudo)"), value=False)
        self.dl = ft.Switch(label=_t("Download to this phone when finished"), value=False)
        self.sched = ft.Switch(label=_t("Also schedule it daily"), value=False, on_change=lambda _: self._sched_changed())
        self.hour = ft.TextField(label=_t("Hour"), value="3", width=90, keyboard_type=ft.KeyboardType.NUMBER, visible=False)
        self.minute = ft.TextField(label=_t("Minute"), value="0", width=90, keyboard_type=ft.KeyboardType.NUMBER, visible=False)
        self.keep = ft.TextField(label=_t("Delete backups older than (days, 0 = never)"), value="14", visible=False,
                                 keyboard_type=ft.KeyboardType.NUMBER)
        self.existing = ft.Column(spacing=6)
        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
            ft.Text(_t("Backups"), size=22, weight=ft.FontWeight.W_600),
            ft.Text(_t("Creates a .tar.gz of a folder on the server (nothing is installed there). You can also download it to "
                       "this phone and schedule it with cron."), size=12, opacity=0.7),
            self.src, self.name, self.dest, self.excl, self.sudo, self.dl, self.sched,
            ft.Row([self.hour, self.minute]), self.keep,
            ft.FilledButton(_t("Back up now"), icon=ft.Icons.BACKUP, on_click=lambda _: self.page.run_task(self.run)),
            ft.Divider(), ft.Row([ft.Text(_t("Existing backups"), weight=ft.FontWeight.W_600, expand=True),
                                  ft.IconButton(ft.Icons.REFRESH, width=44, height=44,
                                                on_click=lambda _: self.page.run_task(self.load))]), self.existing])

    def _sched_changed(self) -> None:
        for c in (self.hour, self.minute, self.keep):
            c.visible = self.sched.value
        self.page.update()

    def _opts(self) -> tuple[str, str, str, list[str]]:
        src = (self.src.value or "").strip().rstrip("/") or "/"
        name = (self.name.value or "").strip() or posixpath.basename(src)
        return src, (self.dest.value or "").strip(), name, [x for x in (self.excl.value or "").split(",") if x.strip()]

    # ---- list ----
    async def load(self) -> None:
        _t = self._t
        d = (self.dest.value or "").strip()
        rows: list[ft.Control] = []
        if self.ctx.ssh.status.value != "connected":
            rows = [ft.Text(_t("Not connected."), opacity=0.7)]
        else:
            try:
                ents = [e for e in await self.ctx.ssh.listdir(d) if not e.is_dir and B.is_backup_file(e.name)]
                for e in sorted(ents, key=lambda x: -x.mtime)[:50]:
                    rows.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                        content=ft.Row([ft.Icon(ft.Icons.ARCHIVE), ft.Column([
                            ft.Text(e.name, size=13, weight=ft.FontWeight.W_600, no_wrap=True),
                            ft.Text(f"{human_size(e.size)} · {fmt_mtime(e.mtime)}", size=11, opacity=0.7)], spacing=0, expand=True),
                            ft.IconButton(ft.Icons.DOWNLOAD, tooltip=_t("Download"), width=44, height=44,
                                          on_click=lambda _, e=e: self._download(e.path, e.name, e.size)),
                            ft.IconButton(ft.Icons.DELETE, tooltip=_t("Delete"), width=44, height=44,
                                          on_click=lambda _, e=e: self._delete(e))],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER)))
                rows = rows or [ft.Text(_t("No backups in that folder yet."), opacity=0.7)]
            except TakError:
                rows = [ft.Text(_t("That folder doesn't exist yet. It's created by the first backup."), opacity=0.7)]
        self.existing.controls = rows
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _download(self, remote: str, name: str, size: int) -> None:
        _t = self._t
        dest = resolve_download_dir(self.ctx.store.data)
        self.ctx.transfers.add_download(remote, str(unique_local_path(dest, name)), name, size)
        toast(self.page, _t("Queued download → {dest}").format(dest=dest))

    def _delete(self, e) -> None:
        _t = self._t
        async def do():
            try:
                await self.ctx.ssh.remove(e.path)
                self.ctx.log_action(f"rm {shlex.quote(e.path)}", 0)
            except TakError as ex:
                toast(self.page, ex.message, error=True)
            await self.load()
        confirm(self.page, _t("Delete this backup?"), e.name, do, yes_label=_t("Delete"))

    # ---- run ----
    async def run(self) -> None:
        ctx = self.ctx
        _t = self._t
        if ctx.ssh.status.value != "connected":
            toast(self.page, _t("Not connected."), error=True)
            return
        src, dest, name, excl = self._opts()
        try:
            cmd, remote = B.archive_command(src, dest, name, time.time(), excl)
            sched_line = None
            if self.sched.value:
                expr = C.build_schedule("daily", hour=int(self.hour.value), minute=int(self.minute.value))
                if (err := C.validate_schedule(expr)):
                    raise ValueError(err)
                sched_line = (expr, B.cron_command(src, dest, name, excl, max(0, int(self.keep.value or 0))))
        except ValueError as e:
            toast(self.page, str(e) if "must" in str(e) or "can't" in str(e) or "isn't" in str(e) or "outside" in str(e) or "needs" in str(e)
                  else _t("Check the numbers you entered."), error=True)
            return
        # size + free-space estimate (read-only)
        try:
            used = F.parse_du_kb(await ctx.ssh.folder_size(src)) or 0
            free = B.parse_df_avail((await ctx.ssh.run("sh -c " + shlex.quote(_FREE_CMD.format(d=shlex.quote(dest))), timeout=20)).stdout) * 1024
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        warn = ("\n⚠ " + _t("Free space ({size}) is less than the folder size.").format(size=human_size(free))
                if free and used and free < used else "")
        body = (f"{src}\n→ {remote}\n\n"
                + _t("Folder size ≈ {size}, free ≈ {free}.").format(size=human_size(used), free=human_size(free))
                + warn
                + ("\n" + _t("A daily cron job will also be added.") if sched_line else ""))
        if not await ctx.ask_yes_no(_t("Create backup?"), body, _t("Back up")):
            return
        rep = await run_in_sheet(ctx, _t("Backup {name}").format(name=name), [Step("tar", cmd, sudo=self.sudo.value)],
                                 {"kind": "backup", "name": f"backup {name}", "command": cmd, "risk": "writes"})
        if rep is None or not rep.ok:
            return
        if self.dl.value:
            try:
                st = await ctx.ssh.stat(remote)
                self._download(remote, posixpath.basename(remote), st.size or 0)
            except TakError as e:
                toast(self.page, e.message, error=True)
        if sched_line:
            await self._install_cron(*sched_line)
        await self.load()

    async def _install_cron(self, expr: str, command: str) -> None:
        ctx = self.ctx
        _t = self._t
        line = f"{expr} {command}"
        if not await ctx.ask_yes_no(_t("Add this cron job?"), f"{C.describe(expr)}\n\n{line}", _t("Add job")):
            return
        tmp = f"/tmp/.takvps-cron-{new_id()}"
        try:
            cur = (await ctx.ssh.run("crontab -l 2>/dev/null", timeout=15)).stdout
            if cur.strip() and ctx.active:                               # keep the previous crontab locally
                d = data_dir() / "crontab_backups"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{ctx.active.id}-me-{int(time.time())}.txt").write_text(cur, "utf-8")
            text = C.serialize(C.parse_crontab(cur) + [C.make_job(expr, command)])
            await ctx.ssh.write_file(tmp, text.encode(), keep_perms=False)
            r = await ctx.ssh.run(f"crontab {shlex.quote(tmp)}", timeout=15)
            ctx.log_action(f"crontab (backup job): {command[:120]}", r.exit_code)
            toast(self.page, _t("Schedule added.") if r.ok else (r.stderr.strip() or _t("crontab rejected the job.")), error=not r.ok)
        except TakError as e:
            toast(self.page, e.message, error=True)
        finally:
            try:
                await ctx.ssh.remove(tmp)
            except TakError:
                pass


def backups_page(ctx: AppContext) -> ft.Control:
    v = BackupsView(ctx)
    ctx.page.run_task(v.load)
    return v.control
