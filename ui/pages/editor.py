"""Code editor: multi-tab, dirty tracking, SFTP save/save-as/reload, find/replace, highlight view.

Approach (first try): editing uses a monospace multiline TextField (Flet can't colour text inside
an input), and a read-only "Highlighted" view renders syntax colours + line numbers.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Optional

import flet as ft

from core.errors import TakError
from services import editor as E
from services import files as F
from services.i18n import translate
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext

MAX_VIEW_LINES = 3000
_KIND_COLOR = {"keyword": ft.Colors.PRIMARY, "string": ft.Colors.TERTIARY, "comment": ft.Colors.OUTLINE,
               "number": ft.Colors.SECONDARY, "tag": ft.Colors.PRIMARY}
BACKUP_SUFFIX = ".takvps.bak"


@dataclass
class Tab:
    id: int
    name: str
    path: Optional[str]
    text: str = ""
    original: str = ""
    encoding: str = "utf-8"
    eol: str = "\n"
    mtime: int = 0
    lang: str = "text"
    sudo_write: bool = False                 # save through `sudo install` (root-owned files)
    after_save: Optional[object] = None      # async callable run after a successful save

    @property
    def dirty(self) -> bool:
        return self.text != self.original


class EditorController:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page, self.s = ctx, ctx.page, ctx.store.data
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        self.tabs: list[Tab] = []
        self.active = 0
        self._next = 1
        self.view_mode = "edit"          # edit | view
        self.find_open = False
        self.cursor = 0
        self.cur_match: Optional[tuple[int, int]] = None
        self._mounted = False
        self._tf: Optional[ft.TextField] = None

    # ================================================================ tab management
    @property
    def tab(self) -> Optional[Tab]:
        return self.tabs[self.active] if self.tabs else None

    def new_tab(self, name: str = "", path: Optional[str] = None, text: str = "", **kw) -> Tab:
        name = name or f"untitled-{self._next}"
        t = Tab(self._next, name, path, text, text, lang=E.language_for(name), **kw)
        self._next += 1
        self.tabs.append(t)
        self.active = len(self.tabs) - 1
        return t

    def open_from_files(self, path: str) -> None:
        self.page.run_task(self._open_and_show, path)

    async def _open_and_show(self, path: str) -> None:
        if await self.open_path(path):
            self.ctx.navigate("editor")

    async def open_path(self, path: str) -> bool:
        for i, t in enumerate(self.tabs):
            if t.path == path:
                self.active = i
                return True
        try:
            data = await self.ctx.ssh.read_file(path, F.EDIT_MAX_BYTES)
            st = await self.ctx.ssh.stat(path)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return False
        text, enc, eol = E.decode_bytes(data)
        self.new_tab(posixpath.basename(path), path, text, encoding=enc, eol=eol, mtime=st.mtime or 0)
        if enc == "latin-1":
            toast(self.page, self._t("Not valid UTF-8 — opened as Latin-1 and will be saved the same way."))
        return True

    async def open_with(self, path: str, text: Optional[str] = None, *, sudo_write: bool = False,
                        after_save=None, name: str = "") -> None:
        """Open `path` for editing. If `text` is given it is a NEW file with that content."""
        for i, t in enumerate(self.tabs):
            if t.path == path:
                self.active = i
                self.ctx.navigate("editor")
                return
        if text is None:
            try:
                data = await self.ctx.ssh.read_file(path, F.EDIT_MAX_BYTES)
                st = await self.ctx.ssh.stat(path)
            except TakError as e:
                toast(self.page, e.message, error=True)
                return
            body, enc, eol = E.decode_bytes(data)
            tab = self.new_tab(posixpath.basename(path), path, body, encoding=enc, eol=eol, mtime=st.mtime or 0)
        else:
            tab = self.new_tab(name or posixpath.basename(path), path, "")
            tab.text = text                              # original stays "" so the file counts as unsaved
        tab.sudo_write, tab.after_save = sudo_write, after_save
        self.ctx.navigate("editor")

    def close_tab(self, t: Tab) -> None:
        def do() -> None:
            i = self.tabs.index(t)
            self.tabs.remove(t)
            self.active = max(0, min(self.active, len(self.tabs) - 1))
            self.cur_match = None
            self._render_all()
            self.page.update()
        if t.dirty:
            confirm(self.page, self._t("Discard changes?"),
                    self._t("“{name}” has unsaved changes.").format(name=t.name), do,
                    yes_label=self._t("Discard"))
        else:
            do()

    def select_tab(self, i: int) -> None:
        self.active, self.cur_match, self.cursor = i, None, 0
        self._render_all()
        self.page.update()

    # ================================================================ saving
    async def save(self, t: Optional[Tab] = None) -> None:
        t = t or self.tab
        if not t:
            return
        if t.path is None:
            await self.save_as(t)
        else:
            await self._write(t, t.path, check_conflict=True)

    async def save_as(self, t: Optional[Tab] = None) -> None:
        t = t or self.tab
        if not t:
            return
        start = posixpath.dirname(t.path) if t.path else (self.ctx.files_state.path or self.ctx.ssh.home)
        path = await self.ctx.ask_text(self._t("Save as"), self._t("Full path on the server"),
                                       ok=self._t("Save"),
                                       value=posixpath.join(start, t.name))
        if path is None:
            return
        path = F.normalize(path.strip())
        if (err := F.valid_name(posixpath.basename(path))):
            toast(self.page, err, error=True)
            return
        try:
            exists = await self.ctx.ssh.exists(path)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        if exists and path != t.path and not await self.ctx.ask_yes_no(
                self._t("Overwrite?"),
                self._t("“{path}” already exists and will be replaced.").format(path=path),
                self._t("Overwrite")):
            return
        await self._write(t, path, check_conflict=False)

    async def _write(self, t: Tab, path: str, check_conflict: bool) -> None:
        ssh = self.ctx.ssh
        if not await self.ctx.safe_confirm(self._t("Save file"), path):
            return
        try:
            if check_conflict and t.mtime:
                cur = (await ssh.stat(path)).mtime or 0
                if cur != t.mtime and not await self.ctx.ask_yes_no(
                        self._t("File changed on the server"),
                        self._t("Someone modified this file after you opened it. Overwrite their changes?"),
                        self._t("Overwrite")):
                    return
            data = E.encode_text(t.text, t.encoding, t.eol)
            if t.sudo_write:
                if self.s.editor_backup and await ssh.exists(path):
                    from shlex import quote
                    await self.ctx.run_privileged(f"cp -a -- {quote(path)} {quote(path + BACKUP_SUFFIX)}")
                await self.ctx.write_privileged(path, data)
            else:
                if self.s.editor_backup and await ssh.exists(path):
                    await ssh.copy(path, path + BACKUP_SUFFIX)
                await ssh.write_file(path, data, keep_perms=True)
            t.mtime = (await ssh.stat(path)).mtime or 0
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        t.path, t.name, t.original = path, posixpath.basename(path), t.text
        t.lang = E.language_for(t.name)
        toast(self.page, self._t("Saved {name}").format(name=t.name))
        self._render_all()
        self.page.update()
        if t.after_save:
            try:
                await t.after_save()
            except Exception:  # noqa: BLE001
                pass

    async def reload(self) -> None:
        t = self.tab
        if not t or not t.path:
            return

        async def do() -> None:
            try:
                data = await self.ctx.ssh.read_file(t.path, F.EDIT_MAX_BYTES)
                t.mtime = (await self.ctx.ssh.stat(t.path)).mtime or 0
            except TakError as e:
                toast(self.page, e.message, error=True)
                return
            t.text, t.encoding, t.eol = E.decode_bytes(data)
            t.original = t.text
            self._render_all()
            self.page.update()
        if t.dirty:
            confirm(self.page, self._t("Reload from server?"), self._t("Your unsaved changes will be lost."), do,
                    yes_label=self._t("Reload"))
        else:
            await do()

    # ================================================================ find / replace
    def _opts(self) -> tuple[str, bool, bool]:
        return self.find_q.value or "", bool(self.chk_case.value), bool(self.chk_re.value)

    def _matches(self) -> list[tuple[int, int]]:
        t = self.tab
        q, case, rx = self._opts()
        try:
            return E.find_all(t.text, q, case, rx) if t else []
        except ValueError as e:
            self.find_info.value = str(e)
            return []

    async def find_next(self, _=None) -> None:
        t = self.tab
        if not t:
            return
        ms = self._matches()
        if not ms:
            if not self.find_info.value.startswith("Invalid"):
                self.find_info.value = self._t("No matches")
            self.cur_match = None
            self.page.update()
            return
        nxt = next((m for m in ms if m[0] >= self.cursor), ms[0])
        self.cur_match, self.cursor = nxt, nxt[1]
        line, col = E.line_col(t.text, nxt[0])
        self.find_info.value = (f"{ms.index(nxt) + 1} {self._t('of')} {len(ms)} · "
                                f"{self._t('Ln')} {line}")
        if self._tf is not None:
            try:
                self._tf.selection = ft.TextSelection(base_offset=nxt[0], extent_offset=nxt[1])
                await self._tf.focus()
            except Exception:  # noqa: BLE001  (selection API differs across Flet versions)
                pass
        self.page.update()

    def _set_text(self, text: str) -> None:
        t = self.tab
        t.text = text
        if self._tf is not None:
            self._tf.value = text
        self._render_tabs()

    async def replace_one(self, _=None) -> None:
        t = self.tab
        if not t:
            return
        if self.cur_match is None:
            await self.find_next()
        if self.cur_match is None:
            return
        s, e = self.cur_match
        q, case, rx = self._opts()
        piece, _n = E.replace_all(t.text[s:e], q, self.find_r.value or "", case, rx)
        self._set_text(t.text[:s] + piece + t.text[e:])
        self.cursor, self.cur_match = s + len(piece), None
        await self.find_next()

    async def replace_every(self, _=None) -> None:
        t = self.tab
        if not t:
            return
        q, case, rx = self._opts()
        try:
            new, n = E.replace_all(t.text, q, self.find_r.value or "", case, rx)
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        if n:
            self._set_text(new)
            self._render_area()
        self.find_info.value = self._t("Replaced {n}").format(n=n)
        self.page.update()

    # ================================================================ UI
    def build(self) -> ft.Control:
        s = self.s
        self.tabs_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=4)
        self.area = ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=4))
        self.status = ft.Text("", size=11, opacity=0.7)
        self.find_q = ft.TextField(hint_text=self._t("Find"), dense=True, expand=True, on_submit=self.find_next,
                                   on_change=lambda _: setattr(self, "cur_match", None))
        self.find_r = ft.TextField(hint_text=self._t("Replace with"), dense=True, expand=True)
        self.chk_case = ft.Checkbox(label="Aa", value=False)
        self.chk_re = ft.Checkbox(label=".*", value=False)
        self.find_info = ft.Text("", size=11, opacity=0.7)
        self.find_bar = ft.Container(visible=self.find_open, padding=theme.SPACE,
                                     bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, content=ft.Column([
            ft.Row([self.find_q, ft.IconButton(ft.Icons.ARROW_DOWNWARD, tooltip=self._t("Next"), width=44, height=44,
                                               on_click=self.find_next)]),
            ft.Row([self.find_r, ft.TextButton(self._t("Replace"), on_click=self.replace_one),
                    ft.TextButton(self._t("All"), on_click=self.replace_every)]),
            ft.Row([self.chk_case, self.chk_re, self.find_info], spacing=theme.SPACE)], spacing=4))

        def ib(icon, tip, fn):
            return ft.IconButton(icon, tooltip=tip, on_click=fn, width=44, height=44)

        toolbar = ft.Row([
            ib(ft.Icons.NOTE_ADD, self._t("New file"), lambda _: self._new()),
            ib(ft.Icons.SAVE, self._t("Save"), lambda _: self.page.run_task(self.save)),
            ib(ft.Icons.SAVE_AS, self._t("Save as"), lambda _: self.page.run_task(self.save_as)),
            ib(ft.Icons.REFRESH, self._t("Reload from server"), lambda _: self.page.run_task(self.reload)),
            ib(ft.Icons.FIND_REPLACE, self._t("Find & replace"), lambda _: self._toggle_find()),
            ib(ft.Icons.PALETTE, self._t("Edit / highlighted view"), lambda _: self._toggle_view()),
            ib(ft.Icons.TEXT_DECREASE, self._t("Smaller text"), lambda _: self._font(-1)),
            ib(ft.Icons.TEXT_INCREASE, self._t("Larger text"), lambda _: self._font(+1)),
            ib(ft.Icons.WRAP_TEXT, self._t("Wrap lines (highlighted view)"), lambda _: self._toggle_wrap()),
            ib(ft.Icons.FORMAT_LIST_NUMBERED, self._t("Toggle line numbers"), lambda _: self._toggle_line_numbers()),
        ], scroll=ft.ScrollMode.AUTO, spacing=0)
        self._render_all()
        self._mounted = True
        return ft.Column([ft.Container(self.tabs_row, padding=ft.Padding.symmetric(horizontal=4)),
                          toolbar, self.find_bar, self.area,
                          ft.Container(self.status, padding=ft.Padding.symmetric(horizontal=theme.SPACE))],
                         expand=True, spacing=0)

    # ---- toolbar actions ----
    def _new(self) -> None:
        self.new_tab()
        self.view_mode = "edit"
        self._render_all()
        self.page.update()

    def _toggle_find(self) -> None:
        self.find_open = not self.find_open
        self.find_bar.visible = self.find_open
        self.page.update()

    def _toggle_view(self) -> None:
        self.view_mode = "view" if self.view_mode == "edit" else "edit"
        self._render_area()
        self.page.update()

    def _font(self, d: int) -> None:
        self.s.editor_font_size = max(9, min(28, self.s.editor_font_size + d))
        self.ctx.store.save()
        self._render_area()
        self.page.update()

    def _toggle_wrap(self) -> None:
        self.s.editor_wrap = not self.s.editor_wrap
        self.ctx.store.save()
        if self.view_mode == "view":
            self._render_area()
        self.page.update()
        toast(self.page, self._t("Wrap applies to the highlighted view; the edit box always wraps."))

    def _toggle_line_numbers(self) -> None:
        """toggle line numbers in the highlighted view."""
        self.show_line_numbers = not getattr(self, "show_line_numbers", True)
        if self.view_mode == "view":
            self._render_area()
        self.page.update()

    # ---- rendering ----
    def _render_all(self) -> None:
        self._render_tabs()
        self._render_area()

    def _render_tabs(self) -> None:
        chips = []
        for i, t in enumerate(self.tabs):
            sel = i == self.active
            chips.append(ft.Container(
                height=40, padding=ft.Padding.only(left=10, right=2), border_radius=theme.RADIUS,
                bgcolor=ft.Colors.PRIMARY_CONTAINER if sel else ft.Colors.SURFACE_CONTAINER_HIGH,
                on_click=lambda _, i=i: self.select_tab(i), ink=True,
                content=ft.Row([ft.Text(("● " if t.dirty else "") + t.name, size=13, no_wrap=True),
                                ft.IconButton(ft.Icons.CLOSE, icon_size=16, width=36, height=36,
                                              on_click=lambda _, t=t: self.close_tab(t))], spacing=2)))
        self.tabs_row.controls = chips
        t = self.tab
        if t:
            line, col = E.line_col(t.text, self.cursor)
            self.status.value = (f"{t.lang} · {t.encoding} · {'CRLF' if t.eol != chr(10) else 'LF'} · "
                                 f"{t.text.count(chr(10)) + 1} {self._t('lines')} · {self._t('Ln')} {line}, {self._t('Col')} {col}"
                                 + (f" · {t.path}" if t.path else f" · {self._t('unsaved file')}"))
        else:
            self.status.value = ""

    def _render_area(self) -> None:
        t = self.tab
        s = self.s
        self._tf = None
        if not t:
            self.area.content = ft.Column([
                ft.Icon(ft.Icons.EDIT_NOTE, size=56, opacity=0.5),
                ft.Text(self._t("No files open"), size=18, weight=ft.FontWeight.W_600),
                ft.Text(self._t("Open a text file from Files, or start a new one."), opacity=0.7),
                ft.FilledButton(self._t("New file"), icon=ft.Icons.NOTE_ADD, on_click=lambda _: self._new())],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER,
                expand=True)
            return
        if self.view_mode == "edit":
            self._tf = ft.TextField(
                value=t.text, multiline=True, expand=True, min_lines=10, border=ft.InputBorder.NONE,
                text_style=ft.TextStyle(font_family="monospace", size=s.editor_font_size),
                on_change=self._on_text, autofocus=False)
            self.area.content = self._tf
            return
        lines = E.tokenize_lines(t.text, t.lang)
        rows: list[ft.Control] = []
        show_ln = getattr(self, "show_line_numbers", True)
        w = len(str(len(lines))) * (s.editor_font_size * 0.65) + 12
        for n, spans in enumerate(lines[:MAX_VIEW_LINES], 1):
            if show_ln:
                rows.append(ft.Row([
                    ft.Text(str(n), width=w, text_align=ft.TextAlign.END, opacity=0.4,
                            font_family="monospace", size=s.editor_font_size),
                    ft.Text(spans=[ft.TextSpan(c or " ", ft.TextStyle(color=_KIND_COLOR.get(k)))
                                   for c, k in spans] or [ft.TextSpan(" ")],
                            font_family="monospace", size=s.editor_font_size, no_wrap=not s.editor_wrap,
                            expand=True)]))
            else:
                rows.append(ft.Text(spans=[ft.TextSpan(c or " ", ft.TextStyle(color=_KIND_COLOR.get(k)))
                               for c, k in spans] or [ft.TextSpan(" ")],
                        font_family="monospace", size=s.editor_font_size, no_wrap=not s.editor_wrap,
                        expand=s.editor_wrap, selectable=True))
        if len(lines) > MAX_VIEW_LINES:
            rows.append(ft.Text(self._t("Showing first {n} of {m} lines.").format(n=MAX_VIEW_LINES, m=len(lines)),
                                size=11, opacity=0.6))
        self.area.content = ft.ListView(rows, spacing=0, expand=True)

    def _on_text(self, e) -> None:
        t = self.tab
        if not t:
            return
        was = t.dirty
        t.text = e.control.value or ""
        try:
            sel = getattr(e, "selection", None) or getattr(e.control, "selection", None)
            self.cursor = int(sel.end) if sel is not None else len(t.text)
        except Exception:  # noqa: BLE001
            self.cursor = len(t.text)
        if was != t.dirty:
            self._render_tabs()
            self.page.update()
