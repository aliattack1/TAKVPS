"""Activity log: everything state-changing this app ran on your servers (stored only on this device)."""
from __future__ import annotations

import asyncio

import flet as ft

from core.fmt import time_ago
from .. import theme
from ..components.common import confirm
from ..context import AppContext
from .chat import RISK_COLORS

FILTERS = [("all", "All"), ("ai", "AI"), ("user", "You"), ("snippet", "Commands"), ("other", "Other")]
_KNOWN = {"ai", "user", "snippet", "deploy"}


def activity_page(ctx: AppContext) -> ft.Control:
    page = ctx.page
    state = {"f": "all", "recs": []}  # recs loaded lazily
    search = ft.TextField(hint_text="Search commands", dense=True, prefix_icon=ft.Icons.SEARCH)
    seg = ft.SegmentedButton(selected=["all"], segments=[ft.Segment(value=k, label=ft.Text(v)) for k, v in FILTERS])
    lst = ft.ListView(expand=True, spacing=6)
    # defer the disk read to a background task so the page
    # renders immediately with a "Loading…" placeholder instead of blocking.
    recs = []  # will be filled by _first()

    def match(r: dict) -> bool:
        f, src = state["f"], r.get("source", "")
        if f == "snippet" and src not in ("snippet", "deploy"):
            return False
        if f == "ai" and src != "ai":
            return False
        if f == "user" and src != "user":
            return False
        if f == "other" and src in _KNOWN:
            return False
        q = (search.value or "").lower()
        return not q or q in r.get("cmd", "").lower() or q in r.get("name", "").lower()

    def detail(r: dict) -> None:
        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(r.get("name") or "Command"), scrollable=True,
            content=ft.Container(width=360, content=ft.Column([
                ft.Text(f"{time_ago(r['ts'])} · source: {r.get('source')} · risk: {r.get('risk')} · exit: {r.get('exit')}", size=12),
                ft.Text(r.get("cmd", ""), font_family="monospace", size=11, selectable=True)], tight=True)),
            actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))

    def draw(_=None) -> None:
        state["f"] = next(iter(seg.selected))
        rows = []
        for r in filter(match, recs):
            ok = r.get("exit") in (0, None)
            src = r.get("source", "")
            rows.append(ft.Container(
                border_radius=theme.RADIUS, padding=theme.SPACE, ink=True, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                on_click=lambda _, r=r: detail(r),
                content=ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR, size=18,
                                        color=ft.Colors.GREEN if ok else ft.Colors.ERROR),
                                ft.Column([ft.Text(r.get("cmd", "").splitlines()[0][:70] if r.get("cmd") else "", font_family="monospace",
                                                   size=11, no_wrap=True),
                                           ft.Text(f"{r.get('name') or '—'} · {time_ago(r['ts'])}", size=10, opacity=0.7)],
                                          spacing=1, expand=True),
                                ft.Container(ft.Text("AI" if src == "ai" else src[:8], size=9, color=ft.Colors.WHITE),
                                             bgcolor=ft.Colors.PRIMARY if src == "ai" else ft.Colors.OUTLINE,
                                             padding=ft.Padding.symmetric(horizontal=5, vertical=1), border_radius=4),
                                ft.Container(width=8, height=8, border_radius=4, bgcolor=RISK_COLORS.get(r.get("risk", "safe")))],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        lst.controls = rows or [ft.Container(padding=24, content=ft.Text(
            "Loading…" if not recs else "Nothing logged yet.", opacity=0.7))]
        page.update()

    def clear(_):
        def do():
            ctx.activity.clear()
            ctx.refresh_ui()
        confirm(page, "Clear the activity log?", "This only removes the history on this device.", do, yes_label="Clear")

    def verify_integrity(_):
        """check the hash chain and show the result."""
        from services.audit_chain import summary_text, is_log_intact
        results = ctx.activity.verify()
        summary = summary_text(results)
        intact = is_log_intact(results)
        color = ft.Colors.GREEN if intact else ft.Colors.ERROR
        icon = ft.Icons.VERIFIED if intact else ft.Icons.WARNING
        page.show_dialog(ft.AlertDialog(
            scrollable=True,
            title=ft.Text("Activity log integrity"), modal=True,
            content=ft.Container(width=360, content=ft.Column([
                ft.Row([ft.Icon(icon, color=color, size=24), ft.Text(summary, size=14, weight=ft.FontWeight.W_600, color=color)]),
                ft.Text("Each record is chained to the previous one via a SHA-256 hash. "
                        "Editing a single line breaks the chain and is detected here." if intact else
                        "The chain is broken — at least one record was edited, inserted, or deleted "
                        "after it was originally logged.",
                        size=12, opacity=0.7),
            ], tight=True, spacing=8)),
            actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))

    seg.on_change = draw
    search.on_change = draw
    # initial fill without page.update (the page is being built)
    state["f"] = "all"
    lst.controls = [ft.Container(padding=24, content=ft.Text("Loading…", opacity=0.7))]
    page.run_task(_first, draw, ctx, recs)
    return ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
        ft.Row([ft.Text("Activity", size=22, weight=ft.FontWeight.W_600, expand=True),
                ft.IconButton(ft.Icons.VERIFIED, tooltip="Verify integrity", width=44, height=44, on_click=verify_integrity),
                ft.IconButton(ft.Icons.DELETE_SWEEP, tooltip="Clear log", width=44, height=44, on_click=clear)], scroll=ft.ScrollMode.AUTO),
        ft.Text("Commands this app ran on your servers (reads are not logged). Stored only on this device.", size=11, opacity=0.6),
        search, seg, lst], expand=True, spacing=8))


async def _first(draw, ctx, recs) -> None:
    """load activity records in the background, then draw."""
    # Defer the disk read so the page renders immediately with "Loading…".
    await asyncio.sleep(0.01)
    recs.extend(ctx.activity.recent(300))
    draw()
