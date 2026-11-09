"""Transfers queue/history dialog."""
from __future__ import annotations

import time
from pathlib import Path

import flet as ft

from core.fmt import human_size
from services.transfers import CANCELLED, DONE, FAILED, QUEUED, RUNNING, Transfer
from ..components.common import toast
from ..context import AppContext

_LABEL = {QUEUED: "Queued", RUNNING: "Running", DONE: "Done", FAILED: "Failed", CANCELLED: "Cancelled"}
_HIST_LABEL = {"done": "Done", "failed": "Failed", "cancelled": "Cancelled"}
_SAVE_COPY_MAX = 50_000_000


class TransfersView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self._picker = None
        self.col = ft.ListView(spacing=8, expand=True)
        self.control = ft.Container(width=340, height=min(420, (page.height or 600) - 200), content=self.col)
        self.refresh(update=False)

    def refresh(self, update: bool = True) -> None:
        """render the live queue, then a persistent 'History' section below it."""
        active = [t for t in self.ctx.transfers.items if t.active]
        finished_live = [t for t in self.ctx.transfers.items if not t.active]
        history = list(reversed(self.ctx.transfer_history.items)) if self.ctx.transfer_history else []
        controls: list[ft.Control] = []
        # --- Active queue ---
        if active:
            controls.append(ft.Text("Active", size=12, weight=ft.FontWeight.W_600, opacity=0.8))
            controls += [self._row(t) for t in active]
        # --- Recently finished (still in the live queue) ---
        if finished_live:
            controls.append(ft.Container(padding=ft.Padding.only(top=4)))
            controls.append(ft.Text("This session", size=12, weight=ft.FontWeight.W_600, opacity=0.8))
            controls += [self._row(t) for t in list(reversed(finished_live))[:20]]
        # --- Persistent history  ---
        if history:
            controls.append(ft.Container(padding=ft.Padding.only(top=4)))
            controls.append(ft.Row([
                ft.Text("History", size=12, weight=ft.FontWeight.W_600, opacity=0.8, expand=True),
                ft.TextButton("Clear history", on_click=lambda _: self._clear_history()),
            ]))
            controls += [self._history_row(h) for h in history[:50]]
        if not controls:
            controls = [ft.Text("No transfers yet.", opacity=0.7)]
        self.col.controls = controls
        if update:
            self.page.update()

    def _row(self, t: Transfer) -> ft.Control:
        sub: list[ft.Control] = []
        size = f" · {human_size(t.total)}" if t.total else ""
        sub.append(ft.Text(_LABEL[t.status] + size + (f" — {t.error}" if t.error else ""),
                           size=11, opacity=0.7,
                           color=ft.Colors.ERROR if t.status == FAILED else None))
        if t.status == RUNNING:
            sub.append(ft.ProgressBar(value=t.fraction))
        if t.status == DONE and t.kind == "down":
            sub.append(ft.Text(t.local, size=10, opacity=0.6, selectable=True))
        trailing: ft.Control = ft.Container(width=44)
        if t.active:
            trailing = ft.IconButton(ft.Icons.CLOSE, tooltip="Cancel", width=44, height=44,
                                     on_click=lambda _, i=t.id: self.ctx.transfers.cancel(i))
        elif t.status == DONE and t.kind == "down":
            trailing = ft.IconButton(ft.Icons.SAVE_ALT, tooltip="Save a copy…", width=44, height=44,
                                     on_click=lambda _, x=t: self.page.run_task(self._save_copy, x))
        return ft.Row([ft.Icon(ft.Icons.DOWNLOAD if t.kind == "down" else ft.Icons.UPLOAD),
                       ft.Column([ft.Text(t.name, no_wrap=True, weight=ft.FontWeight.W_500), *sub],
                                 spacing=2, expand=True), trailing],
                      vertical_alignment=ft.CrossAxisAlignment.START)

    def _history_row(self, h) -> ft.Control:
        """one row per persisted transfer item (read-only)."""
        label = _HIST_LABEL.get(h.status, h.status)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(h.finished or h.created))
        err = f" — {h.error}" if h.error else ""
        size = f" · {human_size(h.total)}" if h.total else ""
        return ft.Row([
            ft.Icon(ft.Icons.DOWNLOAD if h.kind == "down" else ft.Icons.UPLOAD, size=18, opacity=0.7),
            ft.Column([
                ft.Text(h.name, no_wrap=True, size=13),
                ft.Text(f"{label}{size}{err} · {when}", size=11, opacity=0.7),
            ], spacing=2, expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.START)

    def _clear_history(self) -> None:
        if self.ctx.transfer_history:
            self.ctx.transfer_history.clear()
            from services.transfer_history import history_path, save as save_history
            from storage.settings import data_dir
            try:
                save_history(history_path(data_dir()), self.ctx.transfer_history)
            except Exception:  # noqa: BLE001
                pass
            self.refresh()
            toast(self.page, "Transfer history cleared.")

    async def _save_copy(self, t: Transfer) -> None:
        """On Android, the app-private download folder isn't visible in the Files app.
        This lets the user pick a visible location via the system file picker."""
        try:
            p = Path(t.local)
            if not p.exists():
                toast(self.page, "The downloaded file is gone. Re-download it first.", error=True)
                return
            if p.stat().st_size > _SAVE_COPY_MAX:
                toast(self.page, "File is too large to copy this way. Use a file manager to access it directly.", error=True)
                return
            if self._picker is None:
                self._picker = ft.FilePicker()
                self.page.services.append(self._picker)
                self.page.update()
            dest = await self._picker.save_file(file_name=t.name, src_bytes=p.read_bytes())
            if dest:
                toast(self.page, f"Saved to {dest}")
        except Exception as e:  # noqa: BLE001
            # Show the actual path so the user can find it manually
            toast(self.page, f"File is at: {t.local}", error=True)


def open_transfers(ctx: AppContext) -> None:
    view = TransfersView(ctx)
    ctx.transfers_view = view

    def close(_=None) -> None:
        ctx.transfers_view = None
        ctx.page.pop_dialog()

    ctx.page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text("Transfers"), content=view.control,
        on_dismiss=lambda _: setattr(ctx, "transfers_view", None),
        actions=[ft.TextButton("Clear finished", on_click=lambda _: ctx.transfers.clear_finished()),
                 ft.TextButton("Close", on_click=close)]))
