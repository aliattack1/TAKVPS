"""Reusable Commands page: snippets, Git deploy recipes, packs, run history."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Optional

import flet as ft

from core.errors import TakError
from core.fmt import time_ago
from services import deploy as D
from services import packs as K
from services import snippets as S
from services.i18n import translate
from services.risk import classify
from services.runner import Step
from .. import theme
from ..components.common import confirm, empty_state, status_pill, toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext
from .chat import RISK_COLORS

ICONS = ["TERMINAL", "ROCKET_LAUNCH", "UPDATE", "DELETE_SWEEP", "STORAGE", "BUILD", "BACKUP", "PLAY_ARROW",
         "RESTART_ALT", "SECURITY", "SPEED", "CLOUD"]
COLORS = ["#3B82F6", "#22C55E", "#EF4444", "#F59E0B", "#A855F7", "#14B8A6"]

# Risk-level → semantic state for status_pill (modern, theme-aware colors).
_RISK_TO_STATE = {"safe": "ok", "writes": "warning",
                  "destructive": "critical", "dangerous": "critical"}


def _icon(name: str):
    return getattr(ft.Icons, name, ft.Icons.TERMINAL)


def _badge(level: str, lang: str = "en") -> ft.Control:
    """Modern risk-level pill — uses theme STATE_COLORS so the pill picks up
    the active color scheme automatically. Replaces the old plain-color
    chip with white text (which had poor contrast on amber)."""
    return status_pill(level.upper(), _RISK_TO_STATE.get(level, "neutral"), dot=False)


# ====================================================================== page
def snippets_page(ctx: AppContext) -> ft.Control:
    page = ctx.page
    lang = ctx.store.data.language
    _t = lambda s: translate(s, lang)  # noqa: E731
    cid = ctx.active.id if ctx.active else ""
    connected = ctx.ssh.status.value == "connected"
    sns, dps = ctx.snippets.list_snippets(cid), ctx.snippets.list_deploys(cid)

    def snippet_tile(s: S.Snippet) -> ft.Control:
        def delete(_):
            confirm(page, _t("Delete command?"), f"“{s.name}” " + _t("will be removed."),
                    lambda: (ctx.snippets.delete_snippet(s.id), ctx.refresh_ui()),
                    yes_label=_t("Delete"))

        def dup(_):
            ctx.snippets.duplicate_snippet(s.id)
            ctx.refresh_ui()

        def move(d):
            ctx.snippets.move_snippet(s.id, d, cid)
            ctx.refresh_ui()

        menu = ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text(_t("Run in background")), icon=ft.Icons.SCHEDULE,
                             on_click=lambda _: page.run_task(run_snippet, ctx, s, True)),
            ft.PopupMenuItem(content=ft.Text(_t("History")), icon=ft.Icons.HISTORY,
                             on_click=lambda _: open_history(ctx, s.id)),
            ft.PopupMenuItem(content=ft.Text(_t("Edit")), icon=ft.Icons.EDIT,
                             on_click=lambda _: open_snippet_editor(ctx, s)),
            ft.PopupMenuItem(content=ft.Text(_t("Duplicate")), icon=ft.Icons.CONTENT_COPY, on_click=dup),
            ft.PopupMenuItem(content=ft.Text(_t("Move up")), icon=ft.Icons.ARROW_UPWARD, on_click=lambda _: move(-1)),
            ft.PopupMenuItem(content=ft.Text(_t("Move down")), icon=ft.Icons.ARROW_DOWNWARD, on_click=lambda _: move(1)),
            ft.PopupMenuItem(content=ft.Text(_t("Delete")), icon=ft.Icons.DELETE, on_click=delete)])
        # Modern tag pills: risk + flags (sudo/background/scope/asks).
        # Each is a status_pill so they all share the same visual language.
        tags = [_badge(classify(s.command).level, lang)]
        for flag, label, state in (
            (s.sudo, "sudo", "info"),
            (s.background, "background", "neutral"),
            (bool(s.scope), "this server", "neutral"),
            (bool(S.find_variables(s.command)), "asks", "warning"),
        ):
            if flag:
                tags.append(status_pill(label, state, dot=False))
        return ft.Container(
            border_radius=theme.RADIUS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, padding=theme.SPACE,
            content=ft.Row([
                ft.Icon(_icon(s.icon), color=s.color, size=theme.ICON_SIZE_LG),
                ft.Column([ft.Text(s.name, weight=ft.FontWeight.W_600, no_wrap=True),
                           ft.Text((s.command.strip().splitlines()[0][:60] if s.command.strip().splitlines() else ""),
                                   size=11, opacity=0.7, no_wrap=True, font_family="monospace"),
                           ft.Row(tags, spacing=theme.SPACE_XS, wrap=True)], spacing=2, expand=True),
                ft.IconButton(ft.Icons.PLAY_ARROW, tooltip=_t("Run"), disabled=not connected,
                              width=theme.MIN_TAP, height=theme.MIN_TAP,
                              on_click=lambda _: page.run_task(run_snippet, ctx, s, None)),
                menu], vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def deploy_tile(d: D.Deploy) -> ft.Control:
        def delete(_):
            confirm(page, _t("Delete recipe?"), f"“{d.name}” " + _t("will be removed."),
                    lambda: (ctx.snippets.delete_deploy(d.id), ctx.refresh_ui()),
                    yes_label=_t("Delete"))
        menu = ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text(_t("History")), icon=ft.Icons.HISTORY,
                             on_click=lambda _: open_history(ctx, d.id)),
            ft.PopupMenuItem(content=ft.Text(_t("Edit")), icon=ft.Icons.EDIT, on_click=lambda _: open_deploy_editor(ctx, d)),
            ft.PopupMenuItem(content=ft.Text(_t("Delete")), icon=ft.Icons.DELETE, on_click=delete)])
        return ft.Container(
            border_radius=theme.RADIUS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, padding=theme.SPACE,
            content=ft.Row([
                ft.Icon(ft.Icons.ROCKET_LAUNCH, color=ft.Colors.PRIMARY, size=theme.ICON_SIZE_LG),
                ft.Column([ft.Text(d.name, weight=ft.FontWeight.W_600, no_wrap=True),
                           ft.Text(f"{d.project_dir} · {len(d.steps)} {_t('build step(s)')} · "
                                   f"{D.RESTART_KINDS[d.restart_kind]}", size=11, opacity=0.7, no_wrap=True)],
                          spacing=2, expand=True),
                ft.FilledTonalButton(_t("Deploy"), icon=ft.Icons.ROCKET_LAUNCH, disabled=not connected,
                                     height=40, on_click=lambda _: page.run_task(run_deploy, ctx, d)),
                menu], vertical_alignment=ft.CrossAxisAlignment.CENTER))

    items: list[ft.Control] = []
    groups: dict[str, list[S.Snippet]] = {}
    for s in sns:
        groups.setdefault(s.group, []).append(s)
    # Modern section header — slightly larger and more breathing room
    # than the previous plain Text.
    from ..components.common import section_header
    for g in sorted(groups, key=lambda x: (x == "", x.lower())):
        items.append(section_header(g or _t("Ungrouped")))
        items += [snippet_tile(s) for s in groups[g]]
    if dps:
        items.append(section_header(_t("Git deploy recipes")))
        items += [deploy_tile(d) for d in dps]
    if not items:
        # Modern empty state with a primary action button.
        items = [empty_state(
            ft.Icons.BOLT, _t("No commands yet"),
            _t("Save your frequent commands, or import a starter pack from the ⋮ menu."),
            action_label=_t("Add a command"),
            on_action=lambda _: open_snippet_editor(ctx, None))]

    top = ft.Row([
        ft.Text(_t("Commands"), size=22, weight=ft.FontWeight.W_600, expand=True),
        ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text(_t("New Git deploy recipe")), icon=ft.Icons.ROCKET_LAUNCH,
                             on_click=lambda _: open_deploy_editor(ctx, None)),
            ft.PopupMenuItem(content=ft.Text(_t("Packs: import / export")), icon=ft.Icons.INVENTORY_2,
                             on_click=lambda _: open_packs(ctx)),
            ft.PopupMenuItem(content=ft.Text(_t("Run history")), icon=ft.Icons.HISTORY,
                             on_click=lambda _: open_history(ctx, ""))]),
        ft.FilledButton(_t("Add"), icon=ft.Icons.ADD,
                        on_click=lambda _: open_snippet_editor(ctx, None))], scroll=ft.ScrollMode.AUTO)

    # search/filter bar for snippets.
    search = ft.TextField(hint_text=_t("Filter commands by name or text…"), dense=True,
                          prefix_icon=ft.Icons.SEARCH, border_radius=theme.RADIUS_MD)
    filtered_items = list(items)     # will be re-filtered on search

    def do_filter(_=None) -> None:
        q = (search.value or "").lower()
        if not q:
            lst.controls = filtered_items
        else:
            def matches(s):
                return q in s.name.lower() or q in s.command.lower()
            filtered_snippets = [s for s in sns if matches(s)]
            filtered_deploys = [d for d in dps if q in d.name.lower()]
            new_items: list[ft.Control] = []
            fgroups: dict[str, list] = {}
            for s in filtered_snippets:
                fgroups.setdefault(s.group or "", []).append(s)
            for g in sorted(fgroups.keys(), key=lambda x: (x == "", x.lower())):
                new_items.append(section_header(g or _t("Ungrouped")))
                new_items += [snippet_tile(s) for s in fgroups[g]]
            if filtered_deploys:
                new_items.append(section_header(_t("Git deploy recipes")))
                new_items += [deploy_tile(d) for d in filtered_deploys]
            if not new_items:
                # Modern search-no-results state (clear "no matches" rather than
                # the previous raw text).
                new_items = [empty_state(
                    ft.Icons.SEARCH_OFF, _t("No matches"),
                    _t("No commands match “{q}”. Try a different search.").format(q=search.value))]
            lst.controls = new_items
        page.update()

    search.on_change = do_filter
    lst = ft.ListView(items, spacing=theme.SPACE, expand=True)
    return ft.Container(padding=theme.SPACE, expand=True, content=ft.Column(
        [top, search, lst], expand=True, spacing=theme.SPACE))


# ====================================================================== running
async def _ask_variables(ctx: AppContext, sn: S.Snippet, names: list[str]) -> Optional[dict]:
    import asyncio
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    fields = {n: ft.TextField(label=n, autofocus=(i == 0)) for i, n in enumerate(names)}

    def done(v) -> None:
        ctx.page.pop_dialog()
        if not fut.done():
            fut.set_result(v)

    ctx.page.show_dialog(ft.AlertDialog(
        modal=True, title=ft.Text(sn.name), scrollable=True,
        content=ft.Container(width=340, content=ft.Column(
            [ft.Text("Values are inserted shell-quoted.", size=12, opacity=0.7), *fields.values()], tight=True)),
        actions=[ft.TextButton("Cancel", on_click=lambda _: done(None)),
                 ft.FilledButton("Run", on_click=lambda _: done({n: f.value or "" for n, f in fields.items()}))]))
    return await fut


async def run_snippet(ctx: AppContext, sn: S.Snippet, background: Optional[bool] = None) -> None:
    if ctx.ssh.status.value != "connected":
        toast(ctx.page, "Not connected.", error=True)
        return
    names = S.find_variables(sn.command)
    values: dict = {}
    if names:
        values = await _ask_variables(ctx, sn, names)
        if values is None:
            return
    rendered = S.render(sn.command, values)
    bg = sn.background if background is None else background
    if not await ctx.gate_risk(f"Run “{sn.name}”?", [rendered]):
        return
    script = S.build_script(sn.cwd, rendered, bg, sn.name, int(time.time()))
    await run_in_sheet(ctx, sn.name + (" (background)" if bg else ""), [Step(sn.name, script, sudo=sn.sudo)],
                       {"kind": "snippet", "id": sn.id, "name": sn.name, "command": rendered,
                        "risk": classify(rendered).level})


async def run_deploy(ctx: AppContext, d: D.Deploy) -> None:
    if ctx.ssh.status.value != "connected":
        toast(ctx.page, "Not connected.", error=True)
        return
    try:
        steps = D.plan_deploy(d)
        cd = f"cd {S.cd_target(d.project_dir)} && git rev-parse HEAD"
        prev = (await ctx.ssh.run(cd, timeout=20)).stdout.strip()
    except ValueError as e:
        toast(ctx.page, str(e), error=True)
        return
    except TakError as e:
        toast(ctx.page, e.message, error=True)
        return
    if not D.is_sha(prev):
        toast(ctx.page, f"“{d.project_dir}” doesn't look like a git repository.", error=True)
        return
    if not await ctx.gate_risk(f"Deploy “{d.name}”?", [s.cmd for s in steps], force_confirm=True):
        return

    async def rollback() -> None:
        if not await ctx.ask_yes_no(
                "Roll back?", f"This runs `git reset --hard {prev[:8]}` in {d.project_dir} (discarding any "
                "uncommitted changes there), then repeats the build and restart steps.", "Roll back"):
            return
        await run_in_sheet(ctx, f"Rollback {d.name}", D.plan_rollback(d, prev),
                           {"kind": "deploy", "id": d.id, "name": d.name + " (rollback)",
                            "command": f"git reset --hard {prev[:8]}", "risk": "destructive"})

    await run_in_sheet(ctx, f"Deploy {d.name}", steps,
                       {"kind": "deploy", "id": d.id, "name": d.name, "command": f"deploy {d.name}",
                        "risk": "writes"}, rollback=rollback)


# ====================================================================== editors
def open_snippet_editor(ctx: AppContext, existing: Optional[S.Snippet], prefill: str = "") -> None:
    page = ctx.page
    p = existing or S.Snippet(name=(prefill.split() or [""])[0][:30], command=prefill)
    name = ft.TextField(label="Name", value=p.name)
    cmd = ft.TextField(label="Command", value=p.command, multiline=True, min_lines=3, max_lines=8,
                       text_style=ft.TextStyle(font_family="monospace"),
                       hint_text="Use {{name}} for values to ask each time")
    cwd = ft.TextField(label="Working directory (optional)", value=p.cwd, hint_text="/srv/app or ~/project")
    group = ft.TextField(label="Group (optional)", value=p.group, hint_text="e.g. Docker")
    sudo = ft.Switch(label="Run with sudo", value=p.sudo)
    bg = ft.Switch(label="Run in background (nohup, log in /tmp/takvps-logs)", value=p.background)
    scope = ft.Switch(label="Only for this server", value=bool(p.scope), disabled=ctx.active is None)
    icon = ft.Dropdown(label="Icon", value=p.icon if p.icon in ICONS else "TERMINAL",
                       options=[ft.DropdownOption(key=i, text=i.replace("_", " ").title()) for i in ICONS])
    color = [p.color]
    sw = ft.Row(wrap=True)

    def draw():
        sw.controls = [ft.Container(width=36, height=36, border_radius=18, bgcolor=c, data=c,
                                    border=ft.Border.all(3, ft.Colors.ON_SURFACE) if c == color[0] else None,
                                    on_click=pick) for c in COLORS]

    def pick(e):
        color[0] = e.control.data
        draw()
        page.update()
    draw()
    risk = ft.Text("", size=11)

    def on_cmd(_):
        r = classify(cmd.value or "")
        risk.value = f"Risk: {r.level} — {r.explanation}" if (cmd.value or "").strip() else ""
        risk.color = RISK_COLORS[r.level]
        page.update()
    cmd.on_change = on_cmd
    on_cmd(None)

    def save(_):
        s = replace(p, name=name.value.strip(), command=(cmd.value or "").strip("\n"), cwd=(cwd.value or "").strip(),
                    group=(group.value or "").strip(), sudo=sudo.value, background=bg.value,
                    icon=icon.value, color=color[0], scope=(ctx.active.id if scope.value and ctx.active else ""))
        if (err := S.validate(s)):
            toast(page, err, error=True)
            return
        ctx.snippets.save_snippet(s)
        page.pop_dialog()
        ctx.refresh_ui()

    page.show_dialog(ft.AlertDialog(
                title=ft.Text("Edit command" if existing else "New command"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column(
            [name, cmd, risk, cwd, group, sudo, bg, scope, icon, sw], tight=True, spacing=theme.SPACE)),
        actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                 ft.FilledButton("Save", on_click=save)]))


def open_deploy_editor(ctx: AppContext, existing: Optional[D.Deploy]) -> None:
    page = ctx.page
    p = existing or D.Deploy(name="", project_dir="")
    name = ft.TextField(label="Name", value=p.name)
    pdir = ft.TextField(label="Project folder", value=p.project_dir, hint_text="/srv/mysite")
    branch = ft.TextField(label="Branch (optional)", value=p.branch, hint_text="main")
    steps = ft.TextField(label="Build / install steps (one per line)", value="\n".join(p.steps), multiline=True,
                         min_lines=3, max_lines=6, text_style=ft.TextStyle(font_family="monospace"),
                         hint_text="npm ci\nnpm run build")
    kind = ft.Dropdown(label="Then restart", value=p.restart_kind,
                       options=[ft.DropdownOption(key=k, text=v) for k, v in D.RESTART_KINDS.items()])
    target = ft.TextField(label="Service / container / command", value=p.restart_target)
    health = ft.TextField(label="Health check (URL or command, optional)", value=p.health,
                          hint_text="https://example.com/health")
    scope = ft.Switch(label="Only for this server", value=bool(p.scope), disabled=ctx.active is None)

    def save(_):
        d = replace(p, name=name.value.strip(), project_dir=pdir.value.strip(), branch=branch.value.strip(),
                    steps=[x for x in (steps.value or "").splitlines() if x.strip()], restart_kind=kind.value,
                    restart_target=(target.value or "").strip(), health=(health.value or "").strip(),
                    scope=(ctx.active.id if scope.value and ctx.active else ""))
        if (err := D.validate(d)):
            toast(page, err, error=True)
            return
        ctx.snippets.save_deploy(d)
        page.pop_dialog()
        ctx.refresh_ui()

    page.show_dialog(ft.AlertDialog(
                title=ft.Text("Edit deploy recipe" if existing else "New deploy recipe"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column([
            name, pdir, branch, steps, kind, target, health, scope,
            ft.Text("Deploy: git pull → build steps → restart → health check. If a step fails you can roll back "
                    "to the previous commit.", size=11, opacity=0.7)], tight=True, spacing=theme.SPACE)),
        actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                 ft.FilledButton("Save", on_click=save)]))


# ====================================================================== packs
def open_packs(ctx: AppContext) -> None:
    page = ctx.page

    def builtin(name: str):
        try:
            open_import_preview(ctx, K.builtin(name))
        except K.PackError as e:
            toast(page, str(e), error=True)

    text = ft.TextField(label="Paste pack text to import", multiline=True, min_lines=3, max_lines=6)

    def do_paste(_):
        try:
            pack = K.parse_pack(text.value or "")
        except K.PackError as e:
            toast(page, str(e), error=True)
            return
        open_import_preview(ctx, pack)

    page.show_dialog(ft.AlertDialog(
                title=ft.Text("Snippet packs"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column([
            ft.Text("Built-in packs", weight=ft.FontWeight.W_600),
            *[ft.TextButton(n, icon=ft.Icons.INVENTORY_2, on_click=lambda _, n=n: builtin(n)) for n in K.BUILTIN_PACKS],
            ft.Divider(), text, ft.FilledTonalButton("Preview import", on_click=do_paste),
            ft.Divider(), ft.FilledTonalButton("Export my commands…", icon=ft.Icons.IOS_SHARE,
                                               on_click=lambda _: open_export(ctx))], tight=True)),
        actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))


def open_import_preview(ctx: AppContext, pack: K.Pack) -> None:
    """Every command is risk-checked and listed before anything is imported."""
    page = ctx.page
    rows, checks = [], []
    for kind, item, risk in ([("cmd", s, K.snippet_risk(s)) for s in pack.snippets] +
                             [("deploy", d, K.deploy_risk(d)) for d in pack.deploys]):
        cb = ft.Checkbox(value=risk.level != "dangerous")
        checks.append((cb, item))
        body = item.command if kind == "cmd" else f"deploy: {item.project_dir}"
        rows.append(ft.Row([cb, ft.Column([
            ft.Row([ft.Text(item.name, weight=ft.FontWeight.W_600, expand=True), _badge(risk.level)]),
            ft.Text(body.strip().splitlines()[0][:80] if body.strip() else "", size=11, font_family="monospace",
                    opacity=0.8),
            ft.Text(risk.explanation, size=10, opacity=0.6)], spacing=1, expand=True)],
            vertical_alignment=ft.CrossAxisAlignment.START))

    def do_import(_):
        chosen = [i for cb, i in checks if cb.value]
        sns = [i for i in chosen if isinstance(i, S.Snippet)]
        dps = [i for i in chosen if isinstance(i, D.Deploy)]
        ctx.snippets.add_many(sns, dps, "")
        page.pop_dialog()
        ctx.refresh_ui()
        toast(page, f"Imported {len(sns) + len(dps)} item(s).")

    page.show_dialog(ft.AlertDialog(
                title=ft.Text(f"Import “{pack.name}”"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column([
            ft.Text("Review each command. Dangerous ones are unchecked by default.", size=12), *rows],
            tight=True, spacing=theme.SPACE)),
        actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                 ft.FilledButton("Import selected", on_click=do_import)]))


def open_export(ctx: AppContext) -> None:
    page = ctx.page
    cid = ctx.active.id if ctx.active else ""
    sns, dps = ctx.snippets.list_snippets(cid), ctx.snippets.list_deploys(cid)
    checks = [(ft.Checkbox(label=s.name, value=True), s) for s in sns] + \
             [(ft.Checkbox(label=f"{d.name} (deploy)", value=True), d) for d in dps]
    result = ft.TextField(multiline=True, min_lines=4, max_lines=8, read_only=True, visible=False)

    def make(_):
        chosen = [i for cb, i in checks if cb.value]
        result.value = K.export_pack("My TAKVPS pack", [i for i in chosen if isinstance(i, S.Snippet)],
                                     [i for i in chosen if isinstance(i, D.Deploy)])
        result.visible = True
        page.update()

    page.show_dialog(ft.AlertDialog(
                title=ft.Text("Export pack"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column([
            *([c for c, _ in checks] or [ft.Text("Nothing to export yet.")]),
            ft.FilledTonalButton("Create pack text", on_click=make), result,
            ft.Text("Long-press the text to select and copy. Commands are plain text, so don't include secrets.",
                    size=11, opacity=0.7)], tight=True)),
        actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))


# ====================================================================== history
def open_history(ctx: AppContext, item_id: str) -> None:
    page = ctx.page
    runs = ctx.snippet_runs.recent(40, item_id)

    def show(r: dict) -> None:
        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(r["name"]), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([
                ft.Text(f"exit {r['exit']} · {r['sec']} s · {time_ago(r['ts'])}", size=12),
                ft.Text(r.get("out") or "(no output)", font_family="monospace", size=11, selectable=True)],
                tight=True)),
            actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))

    rows = [ft.ListTile(
        leading=ft.Icon(ft.Icons.CHECK_CIRCLE if r["exit"] == 0 else ft.Icons.ERROR,
                        color=ft.Colors.GREEN if r["exit"] == 0 else ft.Colors.ERROR),
        title=ft.Text(r["name"], no_wrap=True), subtitle=ft.Text(f"{time_ago(r['ts'])} · exit {r['exit']} · {r['sec']} s"),
        on_click=lambda _, r=r: show(r)) for r in runs]
    page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text("Run history"),
        content=ft.Container(width=340, height=360, content=ft.ListView(
            rows or [ft.Text("Nothing has been run yet.", opacity=0.7)])),
        actions=[ft.TextButton("Close", on_click=lambda _: page.pop_dialog())]))
