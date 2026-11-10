"""Environment (.env) editor: secrets masked by default, add/remove variables, offer a service restart."""
from __future__ import annotations

import posixpath
import shlex

import flet as ft

from core.errors import RemotePermissionError, TakError
from services import docker as D
from services import editor as ED
from services import envfile as E
from services.i18n import translate
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext


class EnvView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        _t = self._t
        self.from_files = bool(ctx.env_path)
        start = ctx.env_path or posixpath.join(ctx.files_state.path or (ctx.ssh.home if ctx.ssh.status.value == "connected" else "/") or "/", ".env")
        ctx.env_path = ""
        self.path = ft.TextField(label=_t("File path"), value=start, expand=True, on_submit=lambda _: self.page.run_task(self.load))
        self.lines: list[E.EnvLine] = []
        self.orig = ""
        self.enc, self.eol, self.perms, self.uid, self.gid = "utf-8", "\n", 0o600, 0, 0
        self.loaded = False
        self.reveal = ft.Switch(label=_t("Reveal secret values"), value=False, on_change=lambda _: self.render())
        self.shown: set[str] = set()
        self.save_btn = ft.FilledButton(_t("Save"), icon=ft.Icons.SAVE, disabled=True, on_click=lambda _: self.page.run_task(self.save))
        self.list = ft.Column(spacing=6)
        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
            ft.Text(_t("Environment editor"), size=22, weight=ft.FontWeight.W_600),
            ft.Row([self.path, ft.IconButton(ft.Icons.REFRESH, tooltip=_t("Load"), width=44, height=44,
                                             on_click=lambda _: self.page.run_task(self.load))], scroll=ft.ScrollMode.AUTO),
            self.reveal,
            ft.Row([ft.FilledTonalButton(_t("Add variable"), icon=ft.Icons.ADD, on_click=lambda _: self.edit(None)), self.save_btn], scroll=ft.ScrollMode.AUTO, spacing=8),
            self.list,
            ft.Text(_t("Values are masked by default, never shown in logs or the activity log, and never sent to the AI."),
                    size=11, opacity=0.6)])
        self.render()

    @property
    def dirty(self) -> bool:
        return E.serialize(self.lines) != self.orig

    async def load(self) -> None:
        _t = self._t
        p = (self.path.value or "").strip()
        if self.ctx.ssh.status.value != "connected":
            toast(self.page, _t("Not connected."), error=True)
            return
        try:
            data = await self.ctx.ssh.read_file(p, 1_000_000)
            st = await self.ctx.ssh.stat(p)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        text, self.enc, self.eol = ED.decode_bytes(data)
        self.lines, self.orig, self.loaded = E.parse_env(text), text, True
        self.perms, self.uid, self.gid = (st.permissions or 0o600) & 0o7777, st.uid or 0, st.gid or 0
        self.render()

    def render(self) -> None:
        _t = self._t
        rows: list[ft.Control] = []
        for l in self.lines:
            if l.kind != "var":
                continue
            secret = E.is_secret(l.key)
            show = (not secret) or self.reveal.value or l.key in self.shown
            rows.append(ft.Container(border_radius=theme.RADIUS, padding=ft.Padding.symmetric(horizontal=theme.SPACE, vertical=6),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, content=ft.Row([
                    ft.Icon(ft.Icons.KEY if secret else ft.Icons.TUNE, size=18, color=ft.Colors.AMBER_700 if secret else None),
                    ft.Column([ft.Text(l.key, weight=ft.FontWeight.W_600, no_wrap=True),
                               ft.Text(l.value if show else E.mask(l.value), size=11, font_family="monospace", no_wrap=True,
                                       selectable=show, opacity=0.8)], spacing=0, expand=True),
                    ft.IconButton(ft.Icons.VISIBILITY if l.key not in self.shown else ft.Icons.VISIBILITY_OFF, width=40, height=40,
                                  visible=secret, on_click=lambda _, k=l.key: self._toggle(k)),
                    ft.IconButton(ft.Icons.EDIT, width=40, height=40, on_click=lambda _, l=l: self.edit(l)),
                    ft.IconButton(ft.Icons.DELETE, width=40, height=40, on_click=lambda _, k=l.key: self.remove(k))],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        self.list.controls = rows or [ft.Text(_t("Load a file to see its variables.") if not self.loaded else _t("No variables in this file."), opacity=0.7)]
        self.save_btn.disabled = not (self.loaded and self.dirty)
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _toggle(self, k: str) -> None:
        self.shown.symmetric_difference_update({k})
        self.render()

    def remove(self, key: str) -> None:
        _t = self._t
        def do():
            self.lines = E.remove_var(self.lines, key)
            self.render()
        confirm(self.page, _t("Remove variable?"), _t("{key} will be removed when you save.").format(key=key), do, yes_label=_t("Remove"))

    def edit(self, line) -> None:
        page = self.page
        _t = self._t
        key = ft.TextField(label=_t("Name"), value=line.key if line else "", read_only=bool(line), autofocus=not line)
        secret = line is not None and E.is_secret(line.key)
        val = ft.TextField(label=_t("Value"), value=line.value if line else "", multiline=not secret, min_lines=1, max_lines=5,
                           password=secret, can_reveal_password=secret, autofocus=bool(line))

        def go(_):
            k = (key.value or "").strip()
            try:
                self.lines = E.set_var(self.lines, k, val.value or "")
            except ValueError as e:
                toast(page, str(e), error=True)
                return
            page.pop_dialog()
            self.render()
        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(_t("Edit variable") if line else _t("Add variable")), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([key, val], tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton(_t("Cancel"), on_click=lambda _: page.pop_dialog()), ft.FilledButton(_t("OK"), on_click=go)]))

    async def save(self) -> None:
        ctx, p = self.ctx, (self.path.value or "").strip()
        _t = self._t
        if not await ctx.safe_confirm(_t("Save environment file"), p):
            return
        data = ED.encode_text(E.serialize(self.lines), self.enc, self.eol)
        try:
            if ctx.store.data.editor_backup:
                await ctx.ssh.copy(p, p + ".takvps.bak")
            try:
                await ctx.ssh.write_file(p, data, keep_perms=True)
            except RemotePermissionError:
                if not await ctx.ask_yes_no(_t("Administrator rights needed"), _t("You can't write this file as your user. Save it as an "
                                            "administrator (keeping its owner and permissions)?"), _t("Save as admin")):
                    return
                await ctx.write_privileged(p, data, self.perms, str(self.uid), str(self.gid))
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        self.orig = E.serialize(self.lines)
        ctx.log_action(f"edit {shlex.quote(p)} (environment file)", 0)
        toast(self.page, _t("Saved."))
        self.render()
        await self._restart_prompt(p)

    async def _restart_prompt(self, path: str) -> None:
        ctx, page = self.ctx, self.page
        _t = self._t
        saved = ctx.store.data.env_targets.get(path, "")
        kind = ft.Dropdown(label=_t("Restart"), value=("docker" if saved.startswith("docker:") else "systemd" if saved else "none"),
                           options=[ft.DropdownOption(key="none", text=_t("Don't restart")), ft.DropdownOption(key="systemd", text=_t("A systemd service")),
                                    ft.DropdownOption(key="docker", text=_t("A Docker container"))])
        target = ft.TextField(label=_t("Service / container name"), value=saved.split(":", 1)[-1])

        async def go(_):
            page.pop_dialog()
            name = (target.value or "").strip()
            if kind.value == "none":
                return
            ctx.store.data.env_targets[path] = ("docker:" if kind.value == "docker" else "") + name
            ctx.store.save()
            try:
                cmd = f"systemctl restart {shlex.quote(name)}" if kind.value == "systemd" else D.container_command("restart", name)
                if kind.value == "systemd" and not name.replace("-", "").replace("_", "").replace(".", "").replace("@", "").isalnum():
                    raise ValueError(_t("Invalid service name."))
            except ValueError as e:
                toast(page, str(e), error=True)
                return
            if not await ctx.gate_risk(_t("Restart {name}?").format(name=name), [cmd]):
                return
            try:
                r = await ctx.run_privileged(cmd)
            except TakError as e:
                toast(page, e.message, error=True)
                return
            toast(page, _t("{name} restarted.").format(name=name) if r and r.ok else ((r.stderr or r.stdout).strip() if r else _t("Cancelled.")), error=bool(r and not r.ok))

        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(_t("Restart the related service?")), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([ft.Text(_t("Most apps only read their environment at start-up."), size=12),
                                                                kind, target], tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton(_t("Not now"), on_click=lambda _: page.pop_dialog()), ft.FilledButton(_t("Continue"), on_click=go)]))


def envedit_page(ctx: AppContext) -> ft.Control:
    v = EnvView(ctx)
    if v.from_files and ctx.ssh.status.value == "connected":
        ctx.page.run_task(v.load)
    return v.control
