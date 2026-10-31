"""Cron manager: user/root crontab with a schedule builder, safe writes and systemd timers."""
from __future__ import annotations

import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import flet as ft

from core.errors import TakError
from services import cron as C
from services import systemd as SD
from services.snippets import new_id
from storage.settings import data_dir
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext

READ = "crontab {u}-l 2>/dev/null; echo '##NOW'; date '+%Y-%m-%d %H:%M'"
KINDS = [("minutes", "Every N minutes"), ("hourly", "Every hour"), ("daily", "Every day"),
         ("weekly", "Every week"), ("monthly", "Every month"), ("reboot", "At boot"), ("custom", "Custom expression")]


def _backup_dir() -> Path:
    p = data_dir() / "crontab_backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


class CronView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.scope = "me"
        self.lines: list[C.Line] = []
        self.raw = ""
        self.now: Optional[datetime] = None
        self.timers: list[dict] = []
        self.message, self.loading = "", False
        can_root = not (ctx.sysinfo and ctx.sysinfo.sudo == "none")
        self.seg = ft.SegmentedButton(
            selected=["me"], on_change=self._on_scope,
            segments=[ft.Segment(value="me", label=ft.Text("My crontab")),
                      ft.Segment(value="root", label=ft.Text("root's crontab"), disabled=not can_root)])
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Cron", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.PopupMenuButton(items=[
                        ft.PopupMenuItem(content=ft.Text("Edit raw crontab"), icon=ft.Icons.CODE,
                                         on_click=lambda _: self.edit_raw()),
                        ft.PopupMenuItem(content=ft.Text("Restore last backup"), icon=ft.Icons.RESTORE,
                                         on_click=lambda _: self.restore())]),
                    ft.IconButton(ft.Icons.REFRESH, width=44, height=44, tooltip="Refresh",
                                  on_click=lambda _: self.page.run_task(self.load)),
                    ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=lambda _: self.edit_job(None))], scroll=ft.ScrollMode.AUTO),
            self.seg, self.progress, self.list], expand=True, spacing=8))
        self.render()

    def _on_scope(self, e) -> None:
        self.scope = next(iter(e.control.selected))
        self.page.run_task(self.load)

    # ---- load / save ----
    async def load(self) -> None:
        ctx = self.ctx
        self.message = ""
        if ctx.ssh.status.value != "connected":
            self.lines, self.message = [], "Not connected."
            self.render()
            return
        self.loading = True
        self.render()
        try:
            cmd = READ.format(u="-u root " if self.scope == "root" else "")
            r = await (ctx.run_privileged(cmd) if self.scope == "root" else ctx.ssh.run(cmd, timeout=20))
            if r is None:
                self.scope = "me"
                self.seg.selected = ["me"]
                r = await ctx.ssh.run(READ.format(u=""), timeout=20)
            text, _, now = r.stdout.partition("##NOW")
            self.raw = text
            self.lines = C.parse_crontab(text)
            try:
                self.now = datetime.strptime(now.strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                self.now = None
            self.timers = []
            if ctx.sysinfo and ctx.sysinfo.has_systemd:
                self.timers = SD.parse_timers((await ctx.ssh.run("systemctl list-timers --all --no-pager 2>/dev/null",
                                                                 timeout=20)).stdout)
        except TakError as e:
            self.message = e.message
        self.loading = False
        self.render()

    def _backup(self) -> None:
        if not self.raw.strip() or not self.ctx.active:
            return
        prefix = f"{self.ctx.active.id}-{self.scope}-"
        d = _backup_dir()
        (d / f"{prefix}{int(time.time())}.txt").write_text(self.raw, "utf-8")
        for old in sorted(d.glob(prefix + "*.txt"))[:-10]:
            old.unlink(missing_ok=True)

    def _last_backup(self) -> Optional[str]:
        if not self.ctx.active:
            return None
        files = sorted(_backup_dir().glob(f"{self.ctx.active.id}-{self.scope}-*.txt"))
        return files[-1].read_text("utf-8") if files else None

    async def save(self, text: str, ok_msg: str = "Crontab saved.") -> bool:
        ctx = self.ctx
        if not await ctx.safe_confirm("Change crontab", "Your scheduled jobs will change."):
            return False
        tmp = f"/tmp/.takvps-cron-{new_id()}"
        try:
            await ctx.ssh.write_file(tmp, text.encode(), keep_perms=False)
            cmd = f"crontab {shlex.quote(tmp)}" if self.scope == "me" else f"crontab -u root {shlex.quote(tmp)}"
            self._backup()                                    # previous version, kept locally
            r = await (ctx.ssh.run(cmd, timeout=20) if self.scope == "me" else ctx.run_privileged(cmd))
        except TakError as e:
            toast(self.page, e.message, error=True)
            return False
        finally:
            try:
                await ctx.ssh.remove(tmp)
            except TakError:
                pass
        if r is None:
            return False
        if not r.ok:
            self.page.show_dialog(ft.AlertDialog(
                title=ft.Text("crontab rejected the file"), content=ft.Text(
                    (r.stderr or r.stdout).strip() or "Unknown error.", font_family="monospace", size=12, selectable=True),
                actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))
            return False
        toast(self.page, ok_msg)
        await self.load()
        return True

    # ---- rendering ----
    def render(self) -> None:
        self.progress.visible = self.loading
        tiles: list[ft.Control] = []
        jobs = [(i, l) for i, l in enumerate(self.lines) if l.kind == "job"]
        for i, l in jobs:
            tiles.append(self._tile(i, l))
        others = [l.raw for l in self.lines if l.kind != "job" and l.raw.strip()]
        if not jobs and not self.loading:
            tiles.append(ft.Container(padding=24, content=ft.Text(
                self.message or "No scheduled jobs. Tap Add to create one.", opacity=0.7, text_align=ft.TextAlign.CENTER)))
        if others:
            tiles.append(ft.ExpansionTile(title=ft.Text(f"Other lines ({len(others)})"), controls=[
                ft.Container(ft.Text("\n".join(others), font_family="monospace", size=11, selectable=True), padding=12)]))
        if self.timers:
            tiles.append(ft.ExpansionTile(title=ft.Text(f"systemd timers ({len(self.timers)})"), controls=[
                ft.ListTile(title=ft.Text(t["unit"], no_wrap=True), subtitle=ft.Text(t["when"], size=11))
                for t in self.timers[:60]]))
        self.list.controls = tiles
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _tile(self, i: int, l: C.Line) -> ft.Control:
        def toggle(e):
            lines = list(self.lines)
            lines[i] = C.make_job(l.schedule, l.command, e.control.value)
            self.page.run_task(self.save, C.serialize(lines), "Job updated.")

        def delete(_):
            def do():
                lines = list(self.lines)
                del lines[i]
                self.page.run_task(self.save, C.serialize(lines), "Job deleted.")
            confirm(self.page, "Delete job?", f"{l.schedule} {l.command}", do, yes_label="Delete")

        return ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            opacity=1 if l.enabled else 0.55,
            content=ft.Row([
                ft.Switch(value=l.enabled, on_change=toggle),
                ft.Column([ft.Text(C.describe(l.schedule), weight=ft.FontWeight.W_600),
                           ft.Text(l.schedule, size=11, font_family="monospace", opacity=0.7),
                           ft.Text(l.command, size=11, font_family="monospace", no_wrap=True)], spacing=2, expand=True),
                ft.PopupMenuButton(items=[
                    ft.PopupMenuItem(content=ft.Text("Edit"), icon=ft.Icons.EDIT, on_click=lambda _: self.edit_job(i)),
                    ft.PopupMenuItem(content=ft.Text("Delete"), icon=ft.Icons.DELETE, on_click=delete)])],
                vertical_alignment=ft.CrossAxisAlignment.CENTER))

    # ---- dialogs ----
    def edit_job(self, index: Optional[int]) -> None:
        page = self.page
        job = self.lines[index] if index is not None else None
        kind = ft.Dropdown(label="Runs", value="custom" if job else "daily",
                           options=[ft.DropdownOption(key=k, text=v) for k, v in KINDS])
        num = lambda label, v, w=110: ft.TextField(label=label, value=v, width=w, keyboard_type=ft.KeyboardType.NUMBER)  # noqa: E731
        n, minute, hour, dom = num("Every N min", "5"), num("Minute", "0"), num("Hour (0-23)", "3"), num("Day (1-31)", "1")
        dow = ft.Dropdown(label="Weekday", value="1", width=160,
                          options=[ft.DropdownOption(key=str(i), text=d) for i, d in enumerate(C.DAY_NAMES)])
        expr = ft.TextField(label="Cron expression", value=job.schedule if job else "0 3 * * *",
                            hint_text="m h dom mon dow", text_style=ft.TextStyle(font_family="monospace"))
        cmd = ft.TextField(label="Command", value=job.command if job else "", multiline=True, min_lines=2, max_lines=4,
                           text_style=ft.TextStyle(font_family="monospace"),
                           hint_text="/usr/bin/backup.sh >> /var/log/backup.log 2>&1")
        preview = ft.Text("", size=12)
        fields = {"minutes": [n], "hourly": [minute], "daily": [hour, minute], "weekly": [dow, hour, minute],
                  "monthly": [dom, hour, minute], "reboot": [], "custom": [expr]}
        holder = ft.Column(tight=True, spacing=theme.SPACE)

        def current() -> str:
            if kind.value == "custom":
                return expr.value.strip()
            return C.build_schedule(kind.value, n=n.value, minute=minute.value, hour=hour.value, dow=dow.value,
                                    dom=dom.value)

        def refresh(_=None) -> None:
            holder.controls = [ft.Row(fields[kind.value], wrap=True)] if fields[kind.value] else []
            try:
                e = current()
            except ValueError:
                preview.value, preview.color = "Enter whole numbers.", ft.Colors.ERROR
                page.update()
                return
            err = C.validate_schedule(e)
            if err:
                preview.value, preview.color = f"✗ {err}", ft.Colors.ERROR
            else:
                nxt = C.next_runs(e, self.now or datetime.now(), 5)
                when = "\n".join("  " + t.strftime("%a %Y-%m-%d %H:%M") for t in nxt) or "  (runs at boot)"
                preview.value = f"{e}\n{C.describe(e)}\nNext runs (server time):\n{when}"
                preview.color = None
            page.update()

        for f in (kind, n, minute, hour, dom, dow, expr):
            f.on_change = refresh
        kind.on_select = refresh
        dow.on_select = refresh

        async def do_save(_) -> None:
            try:
                e = current()
            except ValueError:
                toast(page, "Enter whole numbers.", error=True)
                return
            if (err := C.validate_job(e, cmd.value or "")):
                toast(page, err, error=True)
                return
            lines = list(self.lines)
            new = C.make_job(e, cmd.value, job.enabled if job else True)
            if index is None:
                lines.append(new)
            else:
                lines[index] = new
            page.pop_dialog()
            await self.save(C.serialize(lines), "Job saved.")

        refresh()
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Edit job" if job else "New job"), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([kind, holder, preview, cmd], tight=True,
                                                              spacing=theme.SPACE)),
            actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton("Save", on_click=do_save)]))

    def edit_raw(self) -> None:
        page = self.page
        tf = ft.TextField(value=self.raw, multiline=True, min_lines=8, max_lines=14,
                          text_style=ft.TextStyle(font_family="monospace", size=12))

        async def do(_):
            page.pop_dialog()
            await self.save(tf.value or "", "Crontab saved.")
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Raw crontab"), scrollable=True,
            content=ft.Container(width=360, content=ft.Column([
                ft.Text("crontab validates the file when saving; the previous version is kept as a local backup.",
                        size=11, opacity=0.7), tf], tight=True)),
            actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton("Save", on_click=do)]))

    def restore(self) -> None:
        text = self._last_backup()
        if text is None:
            toast(self.page, "No backup yet — one is created each time you save.")
            return
        confirm(self.page, "Restore last backup?", "This replaces the current crontab with the most recent backup "
                "(the current one is backed up first).", lambda: self.page.run_task(self.save, text, "Backup restored."),
                yes_label="Restore")


def cron_page(ctx: AppContext) -> ft.Control:
    v = CronView(ctx)
    ctx.page.run_task(v.load)
    return v.control
