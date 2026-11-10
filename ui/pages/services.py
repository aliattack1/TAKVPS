"""systemd service manager."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import systemd as SD
from services.i18n import translate
from .. import theme
from ..components.common import empty_state, toast
from ..components.livelog import open_live_tail
from ..context import AppContext

_ACTIVE = {"active": ft.Colors.GREEN, "failed": ft.Colors.RED, "activating": ft.Colors.AMBER,
           "reloading": ft.Colors.AMBER, "deactivating": ft.Colors.AMBER}
FILTERS = [("all", "All"), ("active", "Active"), ("failed", "Failed"), ("inactive", "Inactive")]
MAX_RENDER = 300


class ServicesView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        _t = self._t
        self.units: list[SD.Unit] = []
        self.flt, self.query = "all", ""
        self.loading, self.message = False, ""
        self.search = ft.TextField(hint_text=_t("Search services"), dense=True, prefix_icon=ft.Icons.SEARCH,
                                   on_change=self._on_query)
        self.seg = ft.SegmentedButton(selected=["all"], on_change=self._on_filter,
                                      segments=[ft.Segment(value=k, label=ft.Text(_t(v))) for k, v in FILTERS])
        self.banner = ft.Container(visible=False, padding=theme.SPACE, border_radius=theme.RADIUS,
                                   bgcolor=ft.Colors.ERROR_CONTAINER)
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text(_t("Services"), size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip=_t("Refresh"), width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.load)),
                    ft.FilledButton(_t("New"), icon=ft.Icons.ADD, on_click=lambda _: self.page.run_task(self.new_unit))], scroll=ft.ScrollMode.AUTO),
            self.progress, self.search, self.seg, self.banner, self.list], expand=True, spacing=8))
        self.render()

    # ---- data ----
    async def load(self) -> None:
        ctx = self.ctx
        _t = self._t
        self.message = ""
        if ctx.ssh.status.value != "connected":
            self.units, self.message = [], _t("Not connected.")
        elif ctx.sysinfo and not ctx.sysinfo.has_systemd:
            self.units, self.message = [], _t("This server doesn't use systemd, so there are no services to manage here.")
        else:
            self.loading = True
            self.render()
            try:
                self.units = SD.parse_units((await ctx.ssh.run(SD.LIST_CMD, timeout=30)).stdout)
                if not self.units:
                    self.message = _t("No services found (is systemd available to this user?).")
            except TakError as e:
                self.message = e.message
        self.loading = False
        self.render()

    def _visible(self) -> list[SD.Unit]:
        q = self.query.lower()
        out = []
        for u in self.units:
            if self.flt == "active" and u.active != "active":
                continue
            if self.flt == "failed" and not u.failed:
                continue
            if self.flt == "inactive" and u.active not in ("inactive", "dead"):
                continue
            if q and q not in u.name.lower() and q not in u.description.lower():
                continue
            out.append(u)
        return out

    def render(self) -> None:
        _t = self._t
        self.progress.visible = self.loading
        self.ctx.index["service"] = [u.short for u in self.units]
        failed = [u for u in self.units if u.failed]
        self.banner.visible = bool(failed) and self.flt != "failed"
        if self.banner.visible:
            self.banner.content = ft.Row([
                ft.Icon(ft.Icons.ERROR), ft.Text(_t("{n} failed: ").format(n=len(failed)) + ", ".join(u.short for u in failed[:3])
                                                 + ("…" if len(failed) > 3 else ""), expand=True),
                ft.TextButton(_t("Show"), on_click=lambda _: self._set_filter("failed"))])
        vis = self._visible()
        tiles = [self._tile(u) for u in vis[:MAX_RENDER]]
        if len(vis) > MAX_RENDER:
            tiles.append(ft.Text(_t("Showing {n1} of {n2}. Use search to narrow down.").format(n1=MAX_RENDER, n2=len(vis)),
                                 size=12, opacity=0.7))
        self.list.controls = tiles or [ft.Container(padding=24, content=ft.Text(
            self.message or (_t("No matching services.") if self.units else _t("Loading…")), opacity=0.7,
            text_align=ft.TextAlign.CENTER))]
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _set_filter(self, k: str) -> None:
        self.flt = k
        self.seg.selected = [k]
        self.render()

    def _on_filter(self, e) -> None:
        self.flt = next(iter(e.control.selected))
        self.render()

    def _on_query(self, e) -> None:
        self.query = e.control.value or ""
        self.render()

    # ---- tiles ----
    def _tile(self, u: SD.Unit) -> ft.Control:
        _t = self._t
        def chip(label: str, color=None) -> ft.Control:
            return ft.Container(ft.Text(label, size=10, color=ft.Colors.WHITE if color else None),
                                bgcolor=color or ft.Colors.SECONDARY_CONTAINER,
                                padding=ft.Padding.symmetric(horizontal=6, vertical=1), border_radius=4)

        def item(label, icon, fn):
            return ft.PopupMenuItem(content=ft.Text(label), icon=icon, on_click=fn)

        def act(verb):
            return lambda _: self.page.run_task(self.act, u, verb)
        menu = ft.PopupMenuButton(items=[
            item(_t("Start"), ft.Icons.PLAY_ARROW, act("start")), item(_t("Stop"), ft.Icons.STOP, act("stop")),
            item(_t("Restart"), ft.Icons.RESTART_ALT, act("restart")), item(_t("Reload"), ft.Icons.REFRESH, act("reload")),
            item(_t("Enable at boot"), ft.Icons.TOGGLE_ON, act("enable")),
            item(_t("Disable at boot"), ft.Icons.TOGGLE_OFF, act("disable")),
            item(_t("Mask"), ft.Icons.BLOCK, act("mask")), item(_t("Unmask"), ft.Icons.CHECK_CIRCLE_OUTLINE, act("unmask")),
            item(_t("Recent logs"), ft.Icons.ARTICLE, lambda _: self.page.run_task(self.logs, u)),
            item(_t("Follow logs (live)"), ft.Icons.PODCASTS, lambda _: self.page.run_task(
                open_live_tail, self.ctx, _t("{name} logs").format(name=u.short), SD.journal_command(u.name, 50, True), True)),
            item(_t("Edit unit file"), ft.Icons.EDIT_NOTE, lambda _: self.page.run_task(self.edit_unit, u))])
        chips = [chip(f"{u.active}/{u.sub}" if u.sub and u.sub != u.active else u.active,
                      _ACTIVE.get(u.active, ft.Colors.OUTLINE))]
        if u.enabled:
            chips.append(chip(u.enabled, ft.Colors.ERROR if u.enabled == "masked" else None))
        return ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE, ink=True,
            bgcolor=ft.Colors.ERROR_CONTAINER if u.failed else ft.Colors.SURFACE_CONTAINER_HIGH,
            on_click=lambda _: self.page.run_task(self.status, u),
            content=ft.Row([ft.Column([ft.Text(u.short, weight=ft.FontWeight.W_600, no_wrap=True),
                                       ft.Text(u.description or "—", size=11, opacity=0.7, no_wrap=True),
                                       ft.Row(chips, spacing=4)], spacing=2, expand=True), menu],
                           vertical_alignment=ft.CrossAxisAlignment.CENTER))

    # ---- actions ----
    async def act(self, u: SD.Unit, verb: str) -> None:
        _t = self._t
        verb_label = {"start": _t("Start"), "stop": _t("Stop"), "restart": _t("Restart"),
                      "reload": _t("Reload"), "enable": _t("Enable"), "disable": _t("Disable"),
                      "mask": _t("Mask"), "unmask": _t("Unmask")}.get(verb, verb.title())
        cmd = SD.action_command(verb, u.name)
        if not await self.ctx.gate_risk(_t("{verb} {name}?").format(verb=verb_label, name=u.short), [cmd]):
            return
        try:
            r = await self.ctx.run_privileged(cmd)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        if r is None:
            return
        if r.ok:
            toast(self.page, _t("{verb} {name}: done").format(verb=verb_label, name=u.short))
        else:
            self._show_text(_t("{verb} failed").format(verb=verb_label),
                            (r.stderr or r.stdout).strip() or _t("No details."))
        await self.load()

    def _show_text(self, title: str, body: str, actions: list | None = None) -> None:
        _t = self._t
        self.page.show_dialog(ft.AlertDialog(
                        title=ft.Text(title, no_wrap=True), scrollable=True,
            content=ft.Container(width=min(380, (page.width or 400) - 32), content=ft.Text(body, font_family="monospace", size=11, selectable=True)),
            actions=(actions or []) + [ft.TextButton(_t("Close"), on_click=lambda _: self.page.pop_dialog())]))

    async def status(self, u: SD.Unit) -> None:
        _t = self._t
        try:
            r = await self.ctx.ssh.run(SD.status_command(u.name), timeout=20)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return

        def go(verb):
            def h(_):
                self.page.pop_dialog()
                self.page.run_task(self.act, u, verb)
            return h
        self._show_text(u.short, r.stdout.strip() or r.stderr.strip() or _t("No status output."), [
            ft.TextButton(_t("Restart"), on_click=go("restart")), ft.TextButton(_t("Stop"), on_click=go("stop")),
            ft.TextButton(_t("Start"), on_click=go("start"))])

    async def logs(self, u: SD.Unit) -> None:
        _t = self._t
        try:
            r = await self.ctx.ssh.run(SD.journal_command(u.name, 200), timeout=30)
            out = r.stdout + r.stderr
            if SD.needs_journal_sudo(out) or not r.stdout.strip():
                r2 = await self.ctx.run_privileged(SD.journal_command(u.name, 200), timeout=30)
                if r2 is not None and r2.stdout.strip():
                    out = r2.stdout
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        self._show_text(_t("{name} · recent logs").format(name=u.short), out.strip() or _t("No log entries."))

    async def _reload_prompt(self) -> None:
        _t = self._t
        if await self.ctx.ask_yes_no(_t("Reload systemd?"), _t("Run `systemctl daemon-reload` so your unit-file changes "
                                     "take effect?"), _t("Reload")):
            r = await self.ctx.run_privileged(SD.DAEMON_RELOAD)
            toast(self.page, _t("systemd reloaded.") if r and r.ok else _t("daemon-reload failed."), error=not (r and r.ok))
            await self.load()

    async def edit_unit(self, u: SD.Unit) -> None:
        _t = self._t
        try:
            path = (await self.ctx.ssh.run(SD.fragment_command(u.name), timeout=15)).stdout.strip()
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        if not path.startswith("/"):
            toast(self.page, _t("This service has no unit file on disk."), error=True)
            return
        if path.startswith(("/usr/lib/", "/lib/")) and not await self.ctx.ask_yes_no(
                _t("Packaged unit file"), _t("This file belongs to an installed package; upgrades may overwrite your "
                "changes. Edit it anyway?"), _t("Edit anyway")):
            return
        await self.ctx.editor.open_with(path, sudo_write=True, after_save=self._reload_prompt)

    async def new_unit(self) -> None:
        _t = self._t
        name = await self.ctx.ask_text(_t("New service"), _t("Name (e.g. myapp)"), ok=_t("Create"))
        if not name:
            return
        path = SD.unit_path(name.strip())
        if not path:
            toast(self.page, _t("Use letters, digits and - _ . only."), error=True)
            return
        try:
            if await self.ctx.ssh.exists(path):
                toast(self.page, _t("That unit already exists."), error=True)
                return
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        user = self.ctx.active.username if self.ctx.active else "root"
        await self.ctx.editor.open_with(path, SD.UNIT_TEMPLATE.format(user=user), sudo_write=True,
                                        after_save=self._reload_prompt)


def services_page(ctx: AppContext) -> ft.Control:
    v = ServicesView(ctx)
    ctx.page.run_task(v.load)
    return v.control
