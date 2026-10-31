"""Command palette: search pages, servers, snippets and names seen this session."""
from __future__ import annotations

import posixpath

import flet as ft

from services.palette import Item, search

_ICON = {"page": ft.Icons.ARROW_FORWARD, "server": ft.Icons.DNS, "snippet": ft.Icons.BOLT, "service": ft.Icons.SETTINGS_SUGGEST,
         "container": ft.Icons.VIEW_IN_AR, "site": ft.Icons.LANGUAGE, "file": ft.Icons.INSERT_DRIVE_FILE, "folder": ft.Icons.FOLDER}
_PAGE_OF = {"service": "services", "container": "docker", "site": "nginx"}


def build_items(ctx) -> list[Item]:
    items = [Item("page", label, "Go to page", key) for key, label in ctx.palette_pages()]
    items += [Item("server", p.name, p.target, p.id) for p in ctx.conns.list()]
    cid = ctx.active.id if ctx.active else ""
    for s in ctx.snippets.list_snippets(cid):
        first = s.command.strip().splitlines()[0][:60] if s.command.strip() else ""
        items.append(Item("snippet", s.name, first, s.id))
    for kind in ("service", "container", "site"):
        items += [Item(kind, n, kind, n) for n in ctx.index.get(kind, [])]
    for entries in list(ctx.files_state.cache.values()):
        for e in entries[:300]:
            items.append(Item("folder" if e.is_dir else "file", e.name, e.path, e.path))
    return items


async def _go(ctx, it: Item) -> None:
    if it.kind == "page":
        ctx.navigate(it.key)
    elif it.kind == "server":
        p = ctx.conns.get(it.key)
        if p and await ctx.connect(p):
            ctx.navigate("overview")
    elif it.kind == "snippet":
        from ..pages.snippets import run_snippet
        sn = ctx.snippets.snippets.get(it.key)
        if sn:
            await run_snippet(ctx, sn)
    elif it.kind in _PAGE_OF:
        ctx.navigate(_PAGE_OF[it.kind])
    elif it.kind in ("file", "folder"):
        ctx.files_state.path = it.key if it.kind == "folder" else posixpath.dirname(it.key) or "/"
        ctx.navigate("files")


def open_palette(ctx) -> None:
    page = ctx.page
    items = build_items(ctx)
    # Phase 18 fix: use expand=True instead of a fixed height so the list
    # shrinks when the keyboard opens on Android (the dialog's height is
    # constrained by the available viewport).
    results = ft.ListView(spacing=0, expand=True)
    field = ft.TextField(hint_text="Search pages, servers, commands, services, files…", autofocus=True,
                         prefix_icon=ft.Icons.SEARCH, dense=True)

    def pick(it: Item) -> None:
        page.pop_dialog()
        page.run_task(_go, ctx, it)

    def draw(_=None) -> None:
        found = search(items, field.value or "", 30)
        results.controls = [ft.ListTile(
            leading=ft.Icon(_ICON.get(it.kind, ft.Icons.CIRCLE), size=20), dense=True,
            title=ft.Text(it.title, no_wrap=True), subtitle=ft.Text(f"{it.kind} · {it.subtitle}" if it.subtitle else it.kind,
                                                                    size=11, no_wrap=True),
            on_click=lambda _, it=it: pick(it)) for it in found] or [ft.Container(padding=20, content=ft.Text("No matches.", opacity=0.7))]
        page.update()

    field.on_change = draw
    field.on_submit = lambda _: (pick(search(items, field.value or "", 1)[0]) if search(items, field.value or "", 1) else None)
    results.controls = []
    # The dialog content has a max height that respects the viewport, so the
    # expand=True ListView will shrink to fit even when the keyboard is open.
    dialog_content = ft.Container(
        width=360,
        height=min(400, (page.height or 600) - 250),  # leave room for dialog chrome + keyboard
        content=ft.Column([field, results], tight=True, expand=True))
    page.show_dialog(ft.AlertDialog(
        title=ft.Text("Search everything"), content=dialog_content,
        actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))
    page.run_task(_first_draw, draw)


async def _first_draw(draw) -> None:
    draw()
