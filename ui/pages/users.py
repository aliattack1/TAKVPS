"""Users, authorized SSH keys and login history."""
from __future__ import annotations

import posixpath
import shlex

import flet as ft

from core.errors import TakError
from services import users as U
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext

TABS = [("users", "Users"), ("keys", "SSH keys"), ("logins", "Logins")]


class UsersView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.mode = "users"
        self.users: list[U.User] = []
        self.show_system = False
        self.key_user = ctx.active.username if ctx.active else ""
        self.lines: list[U.AuthLine] | None = None
        self.last = ""
        self.seg = ft.SegmentedButton(selected=["users"], on_change=self._on_mode,
                                      segments=[ft.Segment(value=k, label=ft.Text(v)) for k, v in TABS])
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Users & keys", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, width=44, height=44, tooltip="Refresh",
                                  on_click=lambda _: self.page.run_task(self.load))], scroll=ft.ScrollMode.AUTO),
            self.seg, self.progress, self.list], expand=True, spacing=8))
        self.render()

    def _on_mode(self, e) -> None:
        self.mode = next(iter(e.control.selected))
        self.page.run_task(self.load)

    # ---- data ----
    @property
    def me(self) -> str:
        return self.ctx.active.username if self.ctx.active else ""

    def _user(self, name: str) -> U.User | None:
        return next((u for u in self.users if u.name == name), None)

    def _keys_path(self, u: U.User) -> str:
        return posixpath.join(u.home, ".ssh", "authorized_keys")

    async def load(self) -> None:
        ctx = self.ctx
        if ctx.ssh.status.value != "connected":
            self.render()
            return
        self.progress.visible = True
        self.page.update()
        try:
            if not self.users:
                self.users = U.parse_passwd((await ctx.ssh.run(U.PASSWD_CMD, timeout=15)).stdout,
                                            (await ctx.ssh.run(U.SUDO_GROUPS_CMD, timeout=15)).stdout)
            if self.mode == "keys":
                await self._load_keys()
            elif self.mode == "logins":
                self.last = (await ctx.ssh.run(U.LAST_CMD, timeout=15)).stdout
        except TakError as e:
            toast(self.page, e.message, error=True)
        self.progress.visible = False
        self.render()

    async def _load_keys(self) -> None:
        u = self._user(self.key_user)
        if u is None:
            self.lines = []
            return
        path = self._keys_path(u)
        if u.name == self.me:
            try:
                text = (await self.ctx.ssh.read_file(path, 500_000)).decode("utf-8", "replace")
            except TakError:
                text = ""                                   # no authorized_keys yet
        else:
            r = await self.ctx.run_privileged(f"cat -- {shlex.quote(path)} 2>/dev/null")
            text = r.stdout if r else ""
        self.lines = U.parse_authorized_keys(text)

    # ---- rendering ----
    def render(self) -> None:
        out: list[ft.Control] = []
        if self.ctx.ssh.status.value != "connected":
            out = [ft.Container(padding=24, content=ft.Text("Not connected.", opacity=0.7))]
        elif self.mode == "users":
            out.append(ft.Switch(label="Show system accounts", value=self.show_system, on_change=self._on_sys))
            for u in [u for u in self.users if self.show_system or u.human]:
                chips = [self._chip("sudo/admin", ft.Colors.AMBER_700)] if u.sudo else []
                out.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, ink=True,
                                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                                        on_click=lambda _, n=u.name: self._open_keys(n),
                                        content=ft.Row([ft.Icon(ft.Icons.PERSON), ft.Column([
                                            ft.Text(u.name + (f"  ({u.gecos})" if u.gecos else ""), weight=ft.FontWeight.W_600),
                                            ft.Text(f"uid {u.uid} · {u.home} · {u.shell}", size=11, opacity=0.7, no_wrap=True),
                                            ft.Row(chips)], spacing=1, expand=True), ft.Icon(ft.Icons.CHEVRON_RIGHT)])))
        elif self.mode == "keys":
            out += self._keys_ui()
        else:
            out += [ft.FilledTonalButton("Failed login attempts", icon=ft.Icons.GPP_BAD,
                                         on_click=lambda _: self.page.run_task(self.failed)),
                    ft.Text("Recent logins", weight=ft.FontWeight.W_600),
                    ft.Text(self.last.strip() or "(no data)", font_family="monospace", size=10, selectable=True)]
        self.list.controls = out
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _chip(self, label: str, color) -> ft.Control:
        return ft.Container(ft.Text(label, size=9, color=ft.Colors.WHITE), bgcolor=color,
                            padding=ft.Padding.symmetric(horizontal=5, vertical=1), border_radius=4)

    def _on_sys(self, e) -> None:
        self.show_system = e.control.value
        self.render()

    def _open_keys(self, name: str) -> None:
        self.key_user, self.mode = name, "keys"
        self.seg.selected = ["keys"]
        self.page.run_task(self.load)

    def _keys_ui(self) -> list[ft.Control]:
        humans = [u.name for u in self.users if u.human]
        out: list[ft.Control] = [
            ft.Dropdown(label="User", value=self.key_user if self.key_user in humans else None,
                        options=[ft.DropdownOption(key=n) for n in humans], on_select=self._on_key_user),
            ft.Row([ft.FilledButton("Add key", icon=ft.Icons.ADD, on_click=lambda _: self.add_key()),
                    ft.Text(f"{self._keys_path(self._user(self.key_user))}" if self._user(self.key_user) else "", size=10, opacity=0.6,
                            expand=True, no_wrap=True)])]
        if self.lines is None:
            return out + [ft.Text("Loading…", opacity=0.7)]
        for i, l in enumerate(self.lines):
            if not l.is_key:
                continue
            out.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Row([ft.Icon(ft.Icons.VPN_KEY), ft.Column([
                    ft.Text(l.comment or "(no comment)", weight=ft.FontWeight.W_600, no_wrap=True),
                    ft.Text(f"{l.type} · {l.fingerprint}", size=10, opacity=0.7, font_family="monospace", selectable=True),
                    ft.Text("options: " + l.options, size=10, opacity=0.6) if l.options else ft.Container()], spacing=1, expand=True),
                    ft.IconButton(ft.Icons.DELETE, width=44, height=44, tooltip="Remove key",
                                  on_click=lambda _, i=i: self.page.run_task(self.remove_key, i))],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        if not any(l.is_key for l in self.lines):
            out.append(ft.Text("No authorized keys for this user.", opacity=0.7))
        return out

    def _on_key_user(self, e) -> None:
        self.key_user = e.control.value
        self.page.run_task(self.load)

    # ---- key changes ----
    async def _save_keys(self, lines: list[U.AuthLine]) -> bool:
        u = self._user(self.key_user)
        if u is None:
            return False
        if not await self.ctx.safe_confirm("Change authorized keys", self.key_user):
            return False
        data = U.serialize(lines).encode()
        path, d = self._keys_path(u), posixpath.dirname(self._keys_path(u))
        try:
            if u.name == self.me:
                await self.ctx.ssh.run(f"mkdir -p {shlex.quote(d)} && chmod 700 {shlex.quote(d)}", timeout=15)
                await self.ctx.ssh.write_file(path, data, keep_perms=False)
                await self.ctx.ssh.chmod(path, 0o600)
            else:
                r = await self.ctx.run_privileged(f"install -d -m 700 -o {shlex.quote(u.name)} -g {shlex.quote(str(u.gid))} {shlex.quote(d)}")
                if r is None or not r.ok:
                    toast(self.page, "Couldn't prepare the .ssh folder.", error=True)
                    return False
                await self.ctx.write_privileged(path, data, 0o600, u.name, str(u.gid))
        except TakError as e:
            toast(self.page, e.message, error=True)
            return False
        self.ctx.log_action(f"edit authorized_keys of {u.name}", 0, "action")
        return True

    def add_key(self) -> None:
        page = self.page
        tf = ft.TextField(label="Public key", multiline=True, min_lines=3, max_lines=6, hint_text="ssh-ed25519 AAAA… name",
                          text_style=ft.TextStyle(font_family="monospace", size=11))

        async def go(_):
            key, err = U.validate_new_key(tf.value or "", self.lines or [])
            if key is None:
                toast(page, err, error=True)
                return
            page.pop_dialog()
            if await self._save_keys((self.lines or []) + [key]):
                toast(page, "Key added.")
                await self.load()

        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(f"Add key for {self.key_user}"), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([tf, ft.Text("Paste the PUBLIC key (the .pub file). Never paste a private key.",
                                                                            size=11, opacity=0.7)], tight=True)),
            actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()), ft.FilledButton("Add", on_click=go)]))

    async def remove_key(self, idx: int) -> None:
        lines = self.lines or []
        k = lines[idx]
        remaining = U.key_count(lines) - 1
        ctx = self.ctx
        mine_key_login = ctx.active and ctx.active.auth == "key" and self.key_user == self.me
        if mine_key_login and remaining == 0:
            if not await ctx.ask_typed("Remove your last key?", "You connect to this server with an SSH key. Removing the last "
                                       "authorized key for this account would lock you out.", "REMOVE"):
                return
        elif mine_key_login and not await ctx.ask_yes_no(
                "Remove key?", f"{k.comment or k.fingerprint}\n\nYou connect with a key. Make sure this isn't the key this app uses, "
                "or you'll lose access.", "Remove"):
            return
        elif not mine_key_login and not await ctx.ask_yes_no("Remove key?", f"{k.comment or k.fingerprint}\n{k.fingerprint}", "Remove"):
            return
        if await self._save_keys(lines[:idx] + lines[idx + 1:]):
            toast(self.page, "Key removed.")
            await self.load()

    async def failed(self) -> None:
        text = ""
        for cmd in U.FAILED_CMDS:
            r = await self.ctx.run_privileged(cmd, 30)
            if r is None:
                return
            if r.stdout.strip():
                text = r.stdout
                break
        ips = U.top_ips(text)
        body = ft.Column([ft.Text("Top sources", weight=ft.FontWeight.W_600)] +
                         [ft.Row([ft.Text(ip, font_family="monospace", expand=True), ft.Text(f"× {n}")]) for ip, n in ips] +
                         [ft.Divider(), ft.Text(text.strip()[-3000:] or "No failed attempts found (or logs aren't readable).",
                                                font_family="monospace", size=10, selectable=True)], tight=True)
        self.page.show_dialog(ft.AlertDialog(title=ft.Text("Failed login attempts"), scrollable=True,
                                             content=ft.Container(width=min(380, (page.width or 400) - 32), content=body),
                                             actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))


def users_page(ctx: AppContext) -> ft.Control:
    v = UsersView(ctx)
    ctx.page.run_task(v.load)
    return v.control
