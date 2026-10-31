"""File manager UI: used as a tab, a docked side panel, or a phone dialog."""
from __future__ import annotations

import os
import posixpath
import stat as st_mod
from typing import Callable, Optional

import flet as ft

from core.errors import TakError
from core.fmt import fmt_mtime, human_size
from core.models import RemoteEntry
from services import envfile as ENV
from services import files as F
from services import recyclebin as RB
from services import folder_transfer as FT
from services.transfers import unique_local_path
from storage.settings import resolve_download_dir
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext
from .transfers import open_transfers

MAX_RENDER = 400
_SORTS = (("name", "Name"), ("size", "Size"), ("date", "Date modified"), ("type", "Type"))


def _kv(label: str, value) -> ft.Control:
    val = value if isinstance(value, ft.Control) else ft.Text(str(value), selectable=True)
    return ft.Row([ft.Text(label, opacity=0.7, width=90), ft.Container(val, expand=True)],
                  vertical_alignment=ft.CrossAxisAlignment.START)


class FileBrowser:
    def __init__(self, ctx: AppContext, compact: bool = False,
                 on_close: Optional[Callable[[], None]] = None) -> None:
        self.ctx, self.page, self.st, self.s = ctx, ctx.page, ctx.files_state, ctx.store.data
        self.compact, self.on_close = compact, on_close
        self.entries: list[RemoteEntry] = list(self.st.cache.get(self.st.path, []))
        self.selected: dict[str, RemoteEntry] = {}
        self.select_mode = False
        self.query = ""
        self.loading = False
        self._gen = 0
        self._picker = None

        self.progress = ft.ProgressBar(visible=False)
        # Transfer status bar: shows active uploads/downloads with progress.
        self.transfer_bar = ft.Container(visible=False, padding=ft.Padding.symmetric(horizontal=theme.SPACE, vertical=4),
                                         bgcolor=ft.Colors.SECONDARY_CONTAINER)
        self.transfer_progress = ft.ProgressBar(visible=False, height=6)
        self.transfer_label = ft.Text("", size=11, no_wrap=True, opacity=0.8)
        self.bookmark_bar = ft.Container(visible=False, padding=ft.Padding.symmetric(horizontal=theme.SPACE, vertical=2),
                                         bgcolor=ft.Colors.TERTIARY_CONTAINER)
        self.crumbs = ft.Row(scroll=ft.ScrollMode.AUTO, auto_scroll=True, spacing=0)
        self.search = ft.TextField(hint_text="Filter this folder", dense=True, visible=False,
                                   prefix_icon=ft.Icons.SEARCH, on_change=self._on_query)
        self.paste_bar = ft.Container(visible=False, padding=theme.SPACE,
                                      bgcolor=ft.Colors.SECONDARY_CONTAINER)
        self.sel_bar = ft.Container(visible=False, padding=ft.Padding.symmetric(horizontal=theme.SPACE),
                                    bgcolor=ft.Colors.SECONDARY_CONTAINER)
        self.list = ft.ListView(expand=True, spacing=0)
        self.toolbar = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=0)
        self.control = ft.Column(
            [self.progress, ft.Container(self.crumbs, padding=ft.Padding.symmetric(horizontal=4)),
             self.toolbar, ft.Container(self.search, padding=ft.Padding.symmetric(horizontal=theme.SPACE)),
             self.transfer_bar, self.transfer_progress, self.transfer_label,
             self.bookmark_bar,
             self.paste_bar, self.list, self.sel_bar], expand=True, spacing=0)
        self._render()
        if ctx.ssh.status.value == "connected":
            self.page.run_task(self.load)
        # Phase 13.4: hook the OS-level drop event (desktop only; no-op on mobile).
        self.attach_drop_zone()
        # Phase 15: register a transfer-tick callback so the transfer progress bar updates.
        ctx._files_transfer_tick = self._on_transfer_tick

    def _on_transfer_tick(self) -> None:
        """Called by ctx._transfers_changed when the transfer queue changes.
        Updates the progress bar and auto-refreshes when uploads finish."""
        self._update_transfer_status()
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ small helpers
    @property
    def cwd(self) -> str:
        return self.st.path or "/"

    def _connected(self) -> bool:
        return self.ctx.ssh.status.value == "connected"

    def _go(self, fn, *args) -> None:
        self.page.run_task(fn, *args)

    def _refresh_ui(self) -> None:
        self._render()
        self._update_transfer_status()
        self.page.update()

    def _err(self, e: Exception) -> None:
        toast(self.page, getattr(e, "message", None) or str(e), error=True)

    def _update_transfer_status(self) -> None:
        """Show a progress bar for active transfers and auto-refresh after they finish."""
        active = [t for t in self.ctx.transfers.items if t.active]
        if active:
            # Show progress for the current transfer.
            t = active[0]
            self.transfer_bar.visible = True
            self.transfer_progress.visible = True
            self.transfer_label.visible = True
            if t.total:
                self.transfer_progress.value = t.done / t.total
                pct = int(t.done / t.total * 100)
                self.transfer_label.value = f"{'↑' if t.kind == 'up' else '↓'} {t.name} · {pct}%"
            else:
                self.transfer_progress.value = None    # indeterminate
                self.transfer_label.value = f"{'↑' if t.kind == 'up' else '↓'} {t.name}…"
        else:
            self.transfer_bar.visible = False
            self.transfer_progress.visible = False
            self.transfer_label.visible = False
            # Check if any transfers just finished → auto-refresh the file list.
            finished = [t for t in self.ctx.transfers.items if not t.active and not getattr(t, "_refreshed", False)]
            if finished:
                for t in finished:
                    t._refreshed = True    # type: ignore[attr-defined]
                # Auto-refresh the file list to show newly uploaded/downloaded files.
                self.page.run_task(self.load, self.cwd)

    # ------------------------------------------------------------ loading
    async def load(self, path: Optional[str] = None) -> None:
        if not self._connected():
            self._refresh_ui()
            return
        target = path or self.st.path or self.ctx.ssh.home
        self._gen += 1
        gen = self._gen
        self.loading = True
        self._refresh_ui()
        try:
            entries = await self.ctx.ssh.listdir(target)
        except TakError as e:
            if gen == self._gen:
                self.loading = False
                self._err(e)
                self._refresh_ui()
            return
        if gen != self._gen:
            return                                         # a newer navigation won
        self.st.path = F.normalize(target)
        self.st.cache[self.st.path] = entries
        self.entries, self.loading = entries, False
        self.selected.clear()
        self.select_mode = False
        self.query = ""
        self.search.value = ""
        self._refresh_ui()

    # ------------------------------------------------------------ rendering
    def _icon_btn(self, icon, tip, fn) -> ft.Control:
        return ft.IconButton(icon, tooltip=tip, on_click=fn, width=44, height=44)

    def _render(self) -> None:
        s = self.s
        self.progress.visible = self.loading
        self._render_bookmarks()
        # toolbar
        sort_items = [ft.PopupMenuItem(
            content=ft.Text(label + ("  ✓" if s.files_sort == key else "")),
            on_click=lambda _, k=key: self._set_sort(k)) for key, label in _SORTS]
        sort_items.append(ft.PopupMenuItem(
            content=ft.Text("Reverse order" + ("  ✓" if s.files_reverse else "")),
            on_click=lambda _: self._toggle_reverse()))
        tools = [
            self._icon_btn(ft.Icons.REFRESH, "Refresh", lambda _: self._go(self.load, self.cwd)),
            self._icon_btn(ft.Icons.SEARCH, "Filter", lambda _: self._toggle_search()),
            ft.PopupMenuButton(icon=ft.Icons.SORT, tooltip="Sort", items=sort_items),
            self._icon_btn(ft.Icons.VISIBILITY if s.files_hidden else ft.Icons.VISIBILITY_OFF,
                           "Hidden files", lambda _: self._toggle_hidden()),
            ft.PopupMenuButton(icon=ft.Icons.ADD, tooltip="New", items=[
                ft.PopupMenuItem(content=ft.Text("New folder"), icon=ft.Icons.CREATE_NEW_FOLDER,
                                 on_click=lambda _: self._go(self.new_folder)),
                ft.PopupMenuItem(content=ft.Text("New file"), icon=ft.Icons.NOTE_ADD,
                                 on_click=lambda _: self._go(self.new_file))]),
            self._icon_btn(ft.Icons.UPLOAD, "Upload", lambda _: self._go(self.upload)),
            self._icon_btn(ft.Icons.STAR, "Bookmark this folder", lambda _: self._toggle_bookmark()),
            self._icon_btn(ft.Icons.DELETE_SWEEP, "Recycle bin", lambda _: self._go(self.open_recycle_bin)),
            self._icon_btn(ft.Icons.SWAP_VERT, "Transfers", lambda _: open_transfers(self.ctx)),
        ]
        if self.compact and self.on_close:
            tools.append(self._icon_btn(ft.Icons.CLOSE, "Close", lambda _: self.on_close()))
        self.toolbar.controls = tools

        # breadcrumbs
        crumbs: list[ft.Control] = []
        for i, (label, p) in enumerate(F.breadcrumbs(self.cwd)):
            if i:
                crumbs.append(ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16, opacity=0.5))
            crumbs.append(ft.TextButton(label, on_click=lambda _, p=p: self._go(self.load, p)))
        self.crumbs.controls = crumbs

        # list
        if not self._connected() and not self.entries:
            self.list.controls = [ft.Container(padding=24, content=ft.Text(
                "Not connected. Connect to a server to browse its files.",
                text_align=ft.TextAlign.CENTER, opacity=0.7))]
        else:
            ents = F.sort_entries(F.filter_entries(self.entries, self.query, s.files_hidden),
                                  s.files_sort, s.files_reverse)
            tiles: list[ft.Control] = []
            if self.cwd != "/":
                tiles.append(ft.ListTile(
                    leading=ft.Icon(ft.Icons.ARROW_UPWARD), title=ft.Text(".."),
                    on_click=lambda _: self._go(self.load, F.parent(self.cwd))))
            tiles += [self._tile(e) for e in ents[:MAX_RENDER]]
            if len(ents) > MAX_RENDER:
                tiles.append(ft.Container(padding=16, content=ft.Text(
                    f"Showing {MAX_RENDER} of {len(ents)} items. Use the filter to narrow down.",
                    size=12, opacity=0.7)))
            if not ents:
                tiles.append(ft.Container(padding=24, content=ft.Text(
                    "No matching items." if self.query else "This folder is empty.", opacity=0.7)))
            self.list.controls = tiles

        # paste bar
        n = len(self.st.clip)
        self.paste_bar.visible = n > 0
        if n:
            verb = "move" if self.st.clip_mode == "move" else "copy"
            self.paste_bar.content = ft.Row([
                ft.Icon(ft.Icons.CONTENT_PASTE), ft.Text(f"{n} item(s) to {verb}", expand=True),
                ft.TextButton("Paste here", on_click=lambda _: self._go(self.paste)),
                ft.IconButton(ft.Icons.CLOSE, width=44, height=44,
                              on_click=lambda _: self._clear_clip())])
        # selection bar
        k = len(self.selected)
        self.sel_bar.visible = self.select_mode and k > 0
        if self.sel_bar.visible:
            one = k == 1
            ents_sel = list(self.selected.values())
            more = ft.PopupMenuButton(items=[
                ft.PopupMenuItem(content=ft.Text("Rename"), icon=ft.Icons.EDIT,
                                 on_click=lambda _: self._go(self.rename, ents_sel[0])),
                ft.PopupMenuItem(content=ft.Text("Permissions"), icon=ft.Icons.LOCK,
                                 on_click=lambda _: self.chmod_dialog(ents_sel[0])),
                ft.PopupMenuItem(content=ft.Text("Properties"), icon=ft.Icons.INFO,
                                 on_click=lambda _: self.properties(ents_sel[0]))]) if one else ft.Container()
            self.sel_bar.content = ft.Row([
                ft.Text(f"{k} selected", expand=True),
                self._icon_btn(ft.Icons.DOWNLOAD, "Download (files & folders)", lambda _: self.download(ents_sel)),
                self._icon_btn(ft.Icons.CONTENT_COPY, "Copy", lambda _: self._set_clip(ents_sel, "copy")),
                self._icon_btn(ft.Icons.CONTENT_CUT, "Move", lambda _: self._set_clip(ents_sel, "move")),
                self._icon_btn(ft.Icons.DELETE, "Delete", lambda _: self.delete(ents_sel)),
                more,
                self._icon_btn(ft.Icons.CLOSE, "Clear selection", lambda _: self._clear_selection())], scroll=ft.ScrollMode.AUTO)

    def _tile(self, e: RemoteEntry) -> ft.Control:
        sel = e.path in self.selected
        if self.select_mode:
            leading: ft.Control = ft.Checkbox(value=sel, on_change=lambda _, e=e: self._toggle(e))
        else:
            icon = (ft.Icons.FOLDER if e.is_dir else ft.Icons.LINK if e.is_link
                    else ft.Icons.INSERT_DRIVE_FILE)
            leading = ft.Icon(icon, color=ft.Colors.AMBER if e.is_dir else None)
        sub = " · ".join(["—" if e.is_dir else human_size(e.size), fmt_mtime(e.mtime),
                          F.perms_string(e.perms, e.is_dir, e.is_link)])
        trailing = None if self.select_mode else ft.PopupMenuButton(items=self._row_menu(e))
        return ft.ListTile(
            leading=leading, title=ft.Text(e.name, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            subtitle=ft.Text(sub, size=11, no_wrap=True), trailing=trailing, selected=sel,
            on_click=lambda _, e=e: self._click(e), on_long_press=lambda _, e=e: self._long(e))

    def _row_menu(self, e: RemoteEntry) -> list[ft.Control]:
        def item(label, icon, fn):
            return ft.PopupMenuItem(content=ft.Text(label), icon=icon, on_click=fn)
        items = []
        if not e.is_dir and ENV.looks_like_env(e.name):
            items.append(item(".env editor (secrets masked)", ft.Icons.KEY, lambda _: self._open_env(e)))
        if not e.is_dir:
            items.append(item("Open in editor", ft.Icons.EDIT_NOTE, lambda _: self._go(self.open_in_editor, e)))
            items.append(item("Download", ft.Icons.DOWNLOAD, lambda _: self.download([e])))
        items += [item("Rename", ft.Icons.EDIT, lambda _: self._go(self.rename, e)),
                  item("Copy", ft.Icons.CONTENT_COPY, lambda _: self._set_clip([e], "copy")),
                  item("Move", ft.Icons.CONTENT_CUT, lambda _: self._set_clip([e], "move")),
                  item("Permissions", ft.Icons.LOCK, lambda _: self.chmod_dialog(e)),
                  item("Properties", ft.Icons.INFO, lambda _: self.properties(e)),
                  item("Delete", ft.Icons.DELETE, lambda _: self.delete([e]))]
        return items

    # ------------------------------------------------------------ toolbar actions
    def _on_query(self, ev) -> None:
        self.query = ev.control.value or ""
        self._refresh_ui()

    def _toggle_search(self) -> None:
        self.search.visible = not self.search.visible
        if not self.search.visible:
            self.query, self.search.value = "", ""
        self._refresh_ui()

    def _set_sort(self, key: str) -> None:
        self.s.files_sort = key
        self.ctx.store.save()
        self._refresh_ui()

    def _toggle_reverse(self) -> None:
        self.s.files_reverse = not self.s.files_reverse
        self.ctx.store.save()
        self._refresh_ui()

    def _toggle_hidden(self) -> None:
        self.s.files_hidden = not self.s.files_hidden
        self.ctx.store.save()
        self._refresh_ui()

    # ------------------------------------------------------------ Phase 17: bookmarks
    def _toggle_bookmark(self) -> None:
        """Bookmark/unbookmark the current folder."""
        path = self.cwd
        bms = list(self.s.file_bookmarks)
        if path in bms:
            bms.remove(path)
            toast(self.page, f"Removed bookmark: {path}")
        else:
            bms.append(path)
            toast(self.page, f"Bookmarked: {path}")
        self.s.file_bookmarks = bms
        self.ctx.store.save()
        self._render_bookmarks()
        self.page.update()

    def _render_bookmarks(self) -> None:
        """Render the bookmark bar with starred folders."""
        bms = list(self.s.file_bookmarks)
        if not bms or not self._connected():
            self.bookmark_bar.visible = False
            return
        chips: list[ft.Control] = [ft.Icon(ft.Icons.STAR, size=14, opacity=0.7)]
        for path in bms[:15]:
            name = path.rsplit("/", 1)[-1] or path
            chips.append(ft.Container(
                ink=True, on_click=lambda _, p=path: self._go(self.load, p),
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                border_radius=12, bgcolor=ft.Colors.PRIMARY_CONTAINER,
                content=ft.Text(name, size=10, no_wrap=True)))
        self.bookmark_bar.visible = True
        self.bookmark_bar.content = ft.Row(chips, spacing=4, scroll=ft.ScrollMode.AUTO)

    # ------------------------------------------------------------ selection / clipboard
    def _click(self, e: RemoteEntry) -> None:
        if self.select_mode:
            self._toggle(e)
        else:
            self._go(self._tap, e)

    def _long(self, e: RemoteEntry) -> None:
        self.select_mode = True
        self.selected[e.path] = e
        self._refresh_ui()

    def _toggle(self, e: RemoteEntry) -> None:
        if e.path in self.selected:
            del self.selected[e.path]
        else:
            self.selected[e.path] = e
        if not self.selected:
            self.select_mode = False
        self._refresh_ui()

    def _clear_selection(self) -> None:
        self.selected.clear()
        self.select_mode = False
        self._refresh_ui()

    def _set_clip(self, entries: list[RemoteEntry], mode: str) -> None:
        self.st.clip, self.st.clip_mode = [e.path for e in entries], mode
        self.selected.clear()
        self.select_mode = False
        self._refresh_ui()
        toast(self.page, "Open the destination folder and tap “Paste here”.")

    def _clear_clip(self) -> None:
        self.st.clip = []
        self._refresh_ui()

    # ------------------------------------------------------------ open
    async def _tap(self, e: RemoteEntry) -> None:
        is_dir = e.is_dir
        if e.is_link:
            try:
                a = await self.ctx.ssh.stat(e.path)
                is_dir = st_mod.S_ISDIR(a.permissions or 0)
            except TakError:
                is_dir = False
        if is_dir:
            await self.load(e.path)
        else:
            await self.open_in_editor(e)

    def _open_env(self, e: RemoteEntry) -> None:
        if self.compact and self.on_close:
            self.on_close()
        self.ctx.env_path = e.path
        self.ctx.navigate("envedit")

    async def open_in_editor(self, e: RemoteEntry) -> None:
        try:
            head = await self.ctx.ssh.read_head(e.path, 4096)
        except TakError as ex:
            self._err(ex)
            return
        status, reason = F.check_editable(e.size, head)
        if status == "refuse":
            toast(self.page, reason, error=True)
        elif status == "warn":
            confirm(self.page, "Open anyway?", reason, lambda: self._launch(e.path), yes_label="Open")
        else:
            self._launch(e.path)

    def _launch(self, path: str) -> None:
        if self.compact and self.on_close:
            self.on_close()                      # close the side panel / phone dialog first
        self.ctx.open_editor(path)

    # ------------------------------------------------------------ create / rename
    async def _create(self, title: str, make) -> None:
        name = await self.ctx.ask_text(title, "Name", ok="Create")
        if name is None:
            return
        name = name.strip()
        if (err := F.valid_name(name)):
            toast(self.page, err, error=True)
            return
        path = F.join(self.cwd, name)
        if not await self.ctx.safe_confirm("Create", path):
            return
        try:
            if await self.ctx.ssh.exists(path):
                toast(self.page, f"“{name}” already exists.", error=True)
                return
            await make(path)
        except TakError as ex:
            self._err(ex)
            return
        await self.load(self.cwd)

    async def new_folder(self) -> None:
        await self._create("New folder", self.ctx.ssh.mkdir)

    async def new_file(self) -> None:
        await self._create("New file", lambda p: self.ctx.ssh.write_file(p, b"", keep_perms=False))

    async def rename(self, e: RemoteEntry) -> None:
        new = await self.ctx.ask_text("Rename", "New name", ok="Rename", value=e.name)
        if new is None or new.strip() == e.name:
            return
        new = new.strip()
        if (err := F.valid_name(new)):
            toast(self.page, err, error=True)
            return
        dst = F.join(F.parent(e.path), new)
        if not await self.ctx.safe_confirm("Rename", f"{e.name} → {new}"):
            return
        try:
            if await self.ctx.ssh.exists(dst):
                toast(self.page, f"“{new}” already exists.", error=True)
                return
            await self.ctx.ssh.rename(e.path, dst)
        except TakError as ex:
            self._err(ex)
            return
        await self.load(self.cwd)

    # ------------------------------------------------------------ delete
    def delete(self, entries: list[RemoteEntry]) -> None:
        """Phase 13.2: delete now goes to the recycle bin (~/.takvps-recycle/)
        so it can be restored. A "Permanently delete" option is offered too."""
        bad = [e for e in entries if F.is_protected(e.path, self.ctx.ssh.home)]
        if bad:
            toast(self.page, f"Refusing to delete “{bad[0].name}”: it is a system or home folder.", error=True)
            return
        # Also check the recyclebin's own protected list (the bin itself, root, etc.)
        for e in entries:
            blocked, reason = RB.is_protected(e.path, self.ctx.ssh.home)
            if blocked:
                toast(self.page, reason, error=True)
                return
        has_dir = any(e.is_dir for e in entries)
        what = f"“{entries[0].name}”" if len(entries) == 1 else f"{len(entries)} items"
        body = (f"Move {what} to the Recycle Bin? You can restore it from there."
                + (" Folders move recursively." if has_dir else ""))

        def _to_bin() -> None:
            self.page.run_task(self._recycle_now, entries)

        def _permanent() -> None:
            old_body = (f"Permanently delete {what}"
                        + (", including everything inside any folders" if has_dir else "")
                        + ". This CANNOT be undone (the recycle bin is bypassed).")
            confirm(self.page, "Permanently delete?", old_body, lambda: self._delete_now(entries),
                    typed="delete" if has_dir else None, yes_label="Delete forever")

        # Use a custom dialog so we can offer both options side by side.
        dlg = ft.AlertDialog(
            title=ft.Text("Delete?"), modal=True,
            content=ft.Container(width=360, content=ft.Text(body)),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                ft.TextButton("Delete forever", on_click=lambda _: (self.page.pop_dialog(), _permanent())),
                ft.FilledTonalButton("Move to bin", on_click=lambda _: (self.page.pop_dialog(), _to_bin())),
            ])
        self.page.show_dialog(dlg)

    async def _recycle_now(self, entries: list[RemoteEntry]) -> None:
        """Move the entries into the recycle bin with a sidecar metadata file each."""
        home = self.ctx.ssh.home
        try:
            await self.ctx.ssh.run(RB.ensure_bin_command(home), timeout=10)
        except TakError as e:
            self._err(e)
            return
        errors, last = 0, None
        for e in entries:
            try:
                entry_name = RB.safe_entry_name(e.path)
                move_cmd, write_meta_cmd = RB.recycle_command(home, e.path, entry_name)
                await self.ctx.ssh.run(move_cmd, timeout=120)
                await self.ctx.ssh.run(write_meta_cmd, timeout=10)
            except TakError as ex:
                errors += 1
                last = ex
        if errors:
            self._err(last)
        else:
            toast(self.page, f"Moved {len(entries)} item(s) to the Recycle Bin.")
        await self.load(self.cwd)

    async def _delete_now(self, entries: list[RemoteEntry]) -> None:
        errors = 0
        for e in entries:
            try:
                await self.ctx.ssh.remove(e.path, recursive=e.is_dir)
            except TakError as ex:
                errors += 1
                last = ex
        if errors:
            self._err(last)
        else:
            toast(self.page, f"Deleted {len(entries)} item(s).")
        await self.load(self.cwd)

    # ------------------------------------------------------------ recycle bin
    async def open_recycle_bin(self) -> None:
        """Show the recycle bin dialog: list, restore, permanently delete, empty."""
        if not self._connected():
            toast(self.page, "Not connected.", error=True)
            return
        home = self.ctx.ssh.home
        try:
            await self.ctx.ssh.run(RB.ensure_bin_command(home), timeout=10)
            r = await self.ctx.ssh.run(RB.list_command(home), timeout=15)
            entries = RB.parse_list(r.stdout)
        except TakError as e:
            self._err(e)
            return
        self._render_recycle_bin_dialog(entries)

    def _render_recycle_bin_dialog(self, entries: list[RB.BinEntry]) -> None:
        home = self.ctx.ssh.home
        col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        if not entries:
            col.controls.append(ft.Text("The Recycle Bin is empty.", size=13, opacity=0.7))
        for e in entries:
            col.controls.append(ft.Row([
                ft.Icon(ft.Icons.FOLDER if e.is_dir else ft.Icons.INSERT_DRIVE_FILE, size=20),
                ft.Column([
                    ft.Text(e.name, no_wrap=True, weight=ft.FontWeight.W_500),
                    ft.Text(f"Was: {e.orig_path}", size=11, opacity=0.7, no_wrap=True),
                    ft.Text(f"{human_size(e.size)} · recycled {fmt_mtime(e.mtime)}",
                            size=11, opacity=0.6, no_wrap=True),
                ], spacing=2, expand=True),
                ft.IconButton(ft.Icons.RESTORE, tooltip="Restore to original path", width=44, height=44,
                              on_click=lambda _, en=e: self.page.run_task(self._restore_entry, en)),
                ft.IconButton(ft.Icons.DELETE_FOREVER, tooltip="Delete forever", width=44, height=44,
                              on_click=lambda _, en=e: self.page.run_task(self._delete_bin_entry, en)),
            ], spacing=4))
        actions = [ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]
        if entries:
            actions.insert(0, ft.TextButton("Empty bin", on_click=lambda _: self.page.run_task(self._empty_bin)))
        dlg = ft.AlertDialog(
            title=ft.Text("Recycle Bin"), modal=True,
            content=ft.Container(width=460, height=420, content=col),
            actions=actions)
        self.page.show_dialog(dlg)

    async def _restore_entry(self, e: RB.BinEntry) -> None:
        home = self.ctx.ssh.home
        # Make sure the original parent dir exists (the user might have deleted
        # the folder it lived in too).
        try:
            await self.ctx.ssh.run(RB.ensure_parent_command(e.orig_path), timeout=10)
            move_cmd, del_meta = RB.restore_command(home, e.name, e.orig_path, overwrite=False)
            # If the destination already exists, refuse — the user has to delete first.
            if await self.ctx.ssh.exists(e.orig_path):
                toast(self.page, f"Refusing to restore: '{e.orig_path}' already exists.", error=True)
                return
            await self.ctx.ssh.run(move_cmd, timeout=60)
            await self.ctx.ssh.run(del_meta, timeout=5)
        except TakError as ex:
            self._err(ex)
            return
        toast(self.page, f"Restored to {e.orig_path}")
        # Refresh the dialog with the remaining entries.
        try:
            r = await self.ctx.ssh.run(RB.list_command(home), timeout=15)
            entries = RB.parse_list(r.stdout)
        except TakError:
            entries = []
        self.page.pop_dialog()
        self._render_recycle_bin_dialog(entries)

    async def _delete_bin_entry(self, e: RB.BinEntry) -> None:
        home = self.ctx.ssh.home
        try:
            await self.ctx.ssh.run(RB.delete_entry_command(home, e.name, e.is_dir), timeout=30)
        except TakError as ex:
            self._err(ex)
            return
        toast(self.page, "Deleted forever.")
        try:
            r = await self.ctx.ssh.run(RB.list_command(home), timeout=15)
            entries = RB.parse_list(r.stdout)
        except TakError:
            entries = []
        self.page.pop_dialog()
        self._render_recycle_bin_dialog(entries)

    async def _empty_bin(self) -> None:
        home = self.ctx.ssh.home
        if not await self.ctx.safe_confirm("Empty Recycle Bin",
                                          "Permanently delete everything in the bin? This cannot be undone."):
            return
        try:
            await self.ctx.ssh.run(RB.empty_bin_command(home), timeout=30)
        except TakError as ex:
            self._err(ex)
            return
        toast(self.page, "Recycle Bin emptied.")
        self.page.pop_dialog()
        self._render_recycle_bin_dialog([])

    # ------------------------------------------------------------ paste
    async def paste(self) -> None:
        clip, mode, ok, skipped = list(self.st.clip), self.st.clip_mode, 0, []
        if not await self.ctx.safe_confirm("Paste", f"{len(clip)} item(s) ({mode}) into {self.cwd}"):
            return
        for src in clip:
            name = F.basename(src)
            dst = F.join(self.cwd, name)
            if F.is_subpath(dst, src) or (mode == "move" and F.parent(src) == self.cwd):
                skipped.append(f"{name} (same location)")
                continue
            try:
                if await self.ctx.ssh.exists(dst):
                    skipped.append(f"{name} (already exists)")
                    continue
                await (self.ctx.ssh.move if mode == "move" else self.ctx.ssh.copy)(src, dst)
                ok += 1
            except TakError as ex:
                skipped.append(f"{name} ({ex.message})")
        self.st.clip = []
        msg = f"{'Moved' if mode == 'move' else 'Copied'} {ok} item(s)."
        if skipped:
            msg += " Skipped: " + "; ".join(skipped[:3]) + ("…" if len(skipped) > 3 else "")
        toast(self.page, msg, error=bool(skipped and not ok))
        await self.load(self.cwd)

    # ------------------------------------------------------------ transfers
    def download(self, entries: list[RemoteEntry]) -> None:
        """Phase 13.3: now supports folders. Folder downloads walk the remote tree
        over SFTP, queue a directory-mkdir step, then enqueue one file transfer per file
        so the existing transfer queue / progress / cancel machinery handles it."""
        if not entries:
            return
        files = [e for e in entries if not e.is_dir]
        folders = [e for e in entries if e.is_dir]
        dest = resolve_download_dir(self.s)
        # Single-file fast path: keep the existing simple queue path.
        if not folders:
            for e in files:
                self.ctx.transfers.add_download(e.path, str(unique_local_path(dest, e.name)), e.name, e.size)
            self._clear_selection()
            toast(self.page, f"Queued {len(files)} download(s) → {dest}")
            return
        # Anything with a folder goes through the folder-transfer flow.
        self.page.run_task(self._download_with_folders, entries, dest)

    async def _download_with_folders(self, entries: list[RemoteEntry], dest_dir) -> None:
        """Walk each folder remotely via SFTP, build a plan, mkdir the local tree,
        then enqueue one file transfer per file."""
        import asyncio
        from pathlib import Path
        ssh = self.ctx.ssh
        # Walk the remote tree under each entry.
        all_files: list[tuple[str, str, int]] = []   # (remote_abs, local_abs, size)
        total_files, total_bytes = 0, 0
        per_folder_dirs: list[list[str]] = []         # relative posix dirs to mkdir on the local side
        try:
            for entry in entries:
                if not entry.is_dir:
                    # Single file: just queue it (mirroring the simple path).
                    local_name = entry.name
                    local_path = str(unique_local_path(dest_dir, local_name))
                    all_files.append((entry.path, local_path, entry.size))
                    total_files += 1
                    total_bytes += entry.size
                    continue
                # Folder: walk recursively via SFTP readdir.
                tree = await self._walk_remote(entry.path)
                plan = FT.build_plan_down(tree, entry.path, str(dest_dir / entry.name), entry.name)
                per_folder_dirs.append([str(Path(dest_dir / entry.name) / d) for d in plan.dirs])
                for src, dst, sz in FT.plan_entries(plan):
                    all_files.append((src, dst, sz))
                total_files += plan.file_count
                total_bytes += plan.total_bytes
        except TakError as e:
            self._err(e)
            return
        if not all_files:
            toast(self.page, "Nothing to download (the folder is empty).")
            return
        # Confirm with a size summary so 5 GB doesn't surprise anyone on mobile data.
        summary = FT.truncate_for_display(
            FT.FolderTransferPlan("down", "summary", "", "", [], [
                FT.TreeNode(rel=f"f{i}", is_dir=False, size=sz) for i, (_, _, sz) in enumerate(all_files)
            ], total_bytes))
        if not await self.ctx.safe_confirm("Download",
                                          f"{total_files} item(s) · {summary}\n→ {dest_dir}"):
            return
        # Create the local dirs first (mkdir is cheap; doing it up-front avoids races
        # with the sequential transfer queue).
        try:
            for dirs in per_folder_dirs:
                FT.make_local_dirs(str(dest_dir), [])
                for d in dirs:
                    Path(d).mkdir(parents=True, exist_ok=True)
            # Local single-file destinations also need their parent.
            for _, local_path, _ in all_files:
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            toast(self.page, f"Couldn't create local folders: {e}", error=True)
            return
        # Enqueue each file.
        for remote, local, size in all_files:
            name = posixpath.basename(remote)
            self.ctx.transfers.add_download(remote, local, name, size)
        self._clear_selection()
        toast(self.page, f"Queued {total_files} download(s) → {dest_dir}")

    async def _walk_remote(self, root: str) -> list[FT.TreeNode]:
        """Recursive readdir. Returns a flat list of TreeNode (relative posix paths,
        is_dir, size). Avoids shell `find` so this works even when the shell is busy."""
        out: list[FT.TreeNode] = []
        # Iterative DFS so deep trees don't blow the recursion limit.
        stack: list[tuple[str, str]] = [(root, "")]
        while stack:
            abs_path, rel = stack.pop()
            try:
                entries = await self.ctx.ssh.listdir(abs_path)
            except TakError:
                continue
            for e in entries:
                child_rel = (rel + "/" + e.name).strip("/") if rel else e.name
                if e.is_dir and not e.is_link:
                    out.append(FT.TreeNode(rel=child_rel, is_dir=True))
                    stack.append((e.path, child_rel))
                else:
                    out.append(FT.TreeNode(rel=child_rel, is_dir=False, size=e.size))
        return out

    async def upload(self) -> None:
        if not self._connected():
            toast(self.page, "Not connected.", error=True)
            return
        try:
            if self._picker is None:
                self._picker = ft.FilePicker()
                self.page.services.append(self._picker)
                self.page.update()
            files = await self._picker.pick_files(allow_multiple=True, dialog_title="Upload to server")
        except Exception:  # noqa: BLE001
            toast(self.page, "Couldn't open the file picker on this platform.", error=True)
            return
        if not files:
            return
        items = [(f.name, f.path) for f in files if f.path]
        if not items:
            toast(self.page, "The picker didn't provide file paths on this platform.", error=True)
            return
        if not await self.ctx.safe_confirm("Upload", f"{len(items)} file(s) to {self.cwd}"):
            return
        existing = {e.name for e in self.entries}
        conflicts = [n for n, _ in items if n in existing]
        dest_dir = self.cwd

        def queue() -> None:
            for name, path in items:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                self.ctx.transfers.add_upload(path, F.join(dest_dir, name), name, size)
            toast(self.page, f"Queued {len(items)} upload(s).")

        if conflicts:
            confirm(self.page, "Overwrite existing files?",
                    f"{len(conflicts)} file(s) already exist here and will be replaced "
                    f"(e.g. “{conflicts[0]}”).", queue, yes_label="Overwrite")
        else:
            queue()

    # ------------------------------------------------------------ Phase 13.4: drag-and-drop
    def attach_drop_zone(self) -> None:
        """Hook the page-level drop event (desktop only) so dragging files from the
        OS file manager onto the file list uploads them. Mobile platforms don't fire
        `page.on_drop`, so this is a no-op there; the Upload button still works."""
        if not self._connected():
            return
        # Don't double-register: the FileBrowser may be rebuilt on every page switch.
        if getattr(self.page, "_takvps_drop_attached", False):
            return
        self.page._takvps_drop_attached = True

        def _on_drop(e) -> None:
            # Only handle drops when the Files page (or its side panel) is the active view.
            files = getattr(e, "files", None) or []
            paths = []
            for f in files:
                p = getattr(f, "path", None) or getattr(f, "name", None)
                if p:
                    paths.append(p)
            if not paths:
                return
            self.page.run_task(self._upload_dropped, paths)

        try:
            self.page.on_drop = _on_drop
        except (AttributeError, TypeError):
            # Older Flet or unsupported platform — silently skip; the Upload button still works.
            pass

    async def _upload_dropped(self, paths: list[str]) -> None:
        """Phase 15 (A1): handle files AND folders dropped from the OS.
        Folders are walked locally and uploaded as a recursive tree."""
        if not self._connected():
            return
        # Separate files from folders
        files, folders = [], []
        for path in paths:
            try:
                if os.path.isdir(path):
                    folders.append(path)
                else:
                    files.append(path)
            except OSError:
                files.append(path)     # treat as file if we can't stat
        total = len(files) + len(folders)
        if not await self.ctx.safe_confirm("Upload dropped items",
                                          f"{total} item(s) dropped onto {self.cwd}"
                                          + (f" ({len(folders)} folder{'s' if len(folders) != 1 else ''})" if folders else "")):
            return
        # Upload single files via the transfer queue (fast path).
        for path in files:
            name = os.path.basename(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            self.ctx.transfers.add_upload(path, F.join(self.cwd, name), name, size)
        # Upload folders via the folder-transfer plan (recursive).
        for folder_path in folders:
            await self._upload_folder_dropped(folder_path)
        if files:
            toast(self.page, f"Queued {len(files)} file(s) for upload.")
        if folders:
            toast(self.page, f"Uploading {len(folders)} folder(s)…")

    async def _upload_folder_dropped(self, local_path: str) -> None:
        """Phase 15 (A1): walk a local folder and upload it recursively."""
        folder_name = os.path.basename(local_path.rstrip("/\\"))
        remote_root = F.join(self.cwd, folder_name)
        try:
            plan = FT.build_plan_up(local_path, remote_root, folder_name)
        except Exception as e:  # noqa: BLE001
            toast(self.page, f"Couldn't walk '{folder_name}': {e}", error=True)
            return
        if not plan.files:
            toast(self.page, f"'{folder_name}' is empty.")
            return
        # Create remote dirs first.
        if plan.dirs:
            try:
                cmd = FT.make_dirs_command([F.join(remote_root, d) for d in plan.dirs])
                await self.ctx.ssh.run(cmd, timeout=30)
            except TakError as e:
                toast(self.page, f"Couldn't create remote dirs: {e.message}", error=True)
                return
        # Queue each file for upload.
        for src, dst, size in FT.plan_entries(plan):
            name = posixpath.basename(dst)
            self.ctx.transfers.add_upload(src, dst, name, size)


    # ------------------------------------------------------------ dialogs
    def properties(self, e: RemoteEntry) -> None:
        kind = "Symlink" if e.is_link else "Folder" if e.is_dir else "File"
        size_txt = ft.Text("—" if e.is_dir else f"{human_size(e.size)} ({e.size:,} bytes)")
        rows: list[ft.Control] = [
            _kv("Name", e.name), _kv("Path", e.path), _kv("Type", kind), _kv("Size", size_txt),
            _kv("Owner", f"{e.owner or '?'}:{e.group or '?'}"),
            _kv("Permissions", f"{F.perms_string(e.perms, e.is_dir, e.is_link)}  ({F.perms_octal(e.perms)})"),
            _kv("Modified", fmt_mtime(e.mtime))]

        async def calc(_) -> None:
            size_txt.value = "Calculating…"
            self.page.update()
            try:
                n = F.parse_du_kb(await self.ctx.ssh.folder_size(e.path))
            except TakError as ex:
                size_txt.value = ex.message
            else:
                size_txt.value = f"{human_size(n)} (approx.)" if n is not None else "Unavailable"
            self.page.update()

        if e.is_dir:
            rows.append(ft.TextButton("Calculate folder size", icon=ft.Icons.CALCULATE, on_click=calc))
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Properties"), scrollable=True,
            content=ft.Container(width=340, content=ft.Column(rows, tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))

    def chmod_dialog(self, e: RemoteEntry) -> None:
        mode = e.perms
        boxes: dict[tuple[int, int], ft.Checkbox] = {}
        octal = ft.TextField(label="Octal", value=f"{mode & 0o777:03o}", width=100,
                             keyboard_type=ft.KeyboardType.NUMBER, max_length=3)
        warn = ft.Text("", size=12, color=ft.Colors.ERROR)

        def bit(r: int, c: int) -> int:
            return 1 << (8 - (r * 3 + c))

        def set_warn(v: int) -> None:
            warn.value = "Anyone on the server will be able to modify this." if v & 0o002 else ""

        def from_boxes(_) -> None:
            v = sum(bit(r, c) for (r, c), cb in boxes.items() if cb.value)
            octal.value = f"{v:03o}"
            set_warn(v)
            self.page.update()

        def from_octal(_) -> None:
            t = octal.value or ""
            if len(t) == 3 and all(ch in "01234567" for ch in t):
                v = int(t, 8)
                for (r, c), cb in boxes.items():
                    cb.value = bool(v & bit(r, c))
                set_warn(v)
                self.page.update()

        octal.on_change = from_octal
        grid: list[ft.Control] = [ft.Row([ft.Container(width=70)] + [
            ft.Text(h, size=12, width=64) for h in ("Read", "Write", "Exec")])]
        for r, who in enumerate(("Owner", "Group", "Others")):
            cells: list[ft.Control] = [ft.Text(who, width=70)]
            for c in range(3):
                cb = ft.Checkbox(value=bool(mode & bit(r, c)), on_change=from_boxes)
                boxes[(r, c)] = cb
                cells.append(ft.Container(cb, width=64))
            grid.append(ft.Row(cells))
        set_warn(mode)

        async def apply(_) -> None:
            t = octal.value or ""
            if len(t) != 3 or any(ch not in "01234567" for ch in t):
                toast(self.page, "Enter 3 octal digits, e.g. 644.", error=True)
                return
            if not await self.ctx.safe_confirm("Change permissions", f"{e.name} → {t}"):
                return
            try:
                await self.ctx.ssh.chmod(e.path, (mode & 0o7000) | int(t, 8))
            except TakError as ex:
                self._err(ex)
                return
            self.page.pop_dialog()
            await self.load(self.cwd)

        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Permissions · {e.name}"), scrollable=True,
            content=ft.Container(width=340, content=ft.Column(grid + [octal, warn], tight=True)),
            actions=[ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                     ft.FilledButton("Apply", on_click=apply)]))


def files_page(ctx: AppContext) -> ft.Control:
    return FileBrowser(ctx).control
