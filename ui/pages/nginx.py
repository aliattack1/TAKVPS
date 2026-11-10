"""Nginx manager: status, safe reload, sites (enable/disable/edit/create), logs, certbot."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
import shlex

from services import nginx as N
from services import packages as P
from services.i18n import translate
from .. import theme
from ..components.common import toast
from ..components.installflow import run_install_flow
from ..components.livelog import open_live_tail
from ..components.runsheet import run_in_sheet
from ..context import AppContext


class NginxView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        self.state: N.NginxState | None = None
        self.sites: list[N.Site] = []
        self.layout = "sites"
        self.loading = False
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text(self._t("Nginx"), size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip=self._t("Refresh"), width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.load))], scroll=ft.ScrollMode.AUTO),
            self.progress, self.list], expand=True, spacing=8))
        self.render()

    @property
    def systemd(self) -> bool:
        return bool(self.ctx.sysinfo and self.ctx.sysinfo.has_systemd)

    # ---- data ----
    async def load(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.state = None
            self.render()
            return
        self.loading = True
        self.render()
        try:
            self.state = N.parse_detect((await self.ctx.ssh.run(N.DETECT_CMD, timeout=25)).stdout)
            if self.state.installed:
                self.sites, self.layout = N.parse_sites((await self.ctx.ssh.run(N.SITES_CMD, timeout=15)).stdout)
                self.ctx.index["site"] = [s.name for s in self.sites]
        except TakError as e:
            toast(self.page, e.message, error=True)
        self.loading = False
        self.render()

    # ---- rendering ----
    def _card(self, controls: list) -> ft.Control:
        return ft.Container(padding=theme.SPACE, border_radius=theme.RADIUS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                            content=ft.Column(controls, spacing=8))

    def render(self) -> None:
        self.progress.visible = self.loading
        st = self.state
        if self.ctx.ssh.status.value != "connected":
            self.list.controls = [ft.Container(padding=24, content=ft.Text(self._t("Not connected."), opacity=0.7))]
        elif st is None:
            self.list.controls = [ft.Container(padding=24, content=ft.Text(self._t("Checking Nginx…"), opacity=0.7))]
        elif not st.installed:
            self.list.controls = [self._card([
                ft.Text(self._t("Nginx isn't installed on this server"), weight=ft.FontWeight.W_600),
                ft.Text(self._t("Installing is optional. You'll see a preflight check and the exact plan, and confirm before "
                                "anything changes."), size=12, opacity=0.7),
                ft.FilledButton(self._t("Install Nginx…"), icon=ft.Icons.DOWNLOAD, on_click=lambda _: self.page.run_task(self.install))])]
        else:
            self.list.controls = self._installed_controls()
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _installed_controls(self) -> list[ft.Control]:
        st = self.state
        active = st.active == "active"

        def btn(label, icon, fn, tonal=True):
            cls = ft.FilledTonalButton if tonal else ft.OutlinedButton
            return cls(label, icon=icon, height=40, on_click=lambda _: self.page.run_task(fn))
        out: list[ft.Control] = [self._card([
            ft.Row([ft.Icon(ft.Icons.CIRCLE, size=12, color=ft.Colors.GREEN if active else ft.Colors.RED),
                    ft.Text(f"nginx {st.version} · {st.active}", weight=ft.FontWeight.W_600, expand=True)]),
            ft.Row([btn(self._t("Reload"), ft.Icons.REFRESH, self.reload), btn(self._t("Test config"), ft.Icons.RULE, self.test),
                    btn(self._t("Restart"), ft.Icons.RESTART_ALT, lambda: self.service("restart")),
                    btn(self._t("Start") if not active else self._t("Stop"), ft.Icons.PLAY_ARROW if not active else ft.Icons.STOP,
                        lambda: self.service("start" if not active else "stop"))], wrap=True, spacing=4, run_spacing=4),
            ft.Text(self._t("Reload and restart always run `nginx -t` first."), size=11, opacity=0.6)])]
        out.append(self._card([
            ft.Text(self._t("Logs & ports"), weight=ft.FontWeight.W_600),
            ft.Row([btn(self._t("Access log"), ft.Icons.ARTICLE, lambda: open_live_tail(
                        self.ctx, self._t("nginx access log"), f"tail -n 100 -F {N.ACCESS_LOG}", True)),
                    btn(self._t("Error log"), ft.Icons.ERROR_OUTLINE, lambda: open_live_tail(
                        self.ctx, self._t("nginx error log"), f"tail -n 100 -F {N.ERROR_LOG}", True)),
                    btn(self._t("Listening ports"), ft.Icons.LAN, self.ports),
                    btn(self._t("HTTPS certificate…"), ft.Icons.LOCK, self.certbot)], wrap=True, spacing=4, run_spacing=4)]))
        out.append(ft.Row([ft.Text(self._t("Sites ({layout})").format(layout='sites-available' if self.layout == 'sites' else 'conf.d'),
                                   weight=ft.FontWeight.W_600, expand=True),
                           ft.FilledButton(self._t("New site"), icon=ft.Icons.ADD, on_click=lambda _: self.page.run_task(self.new_site))]))
        out += [self._site_tile(s) for s in self.sites] or [ft.Text(self._t("No site configs found."), opacity=0.7)]
        return out

    def _site_tile(self, s: N.Site) -> ft.Control:
        return ft.Container(
            border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            content=ft.Row([
                ft.Switch(value=s.enabled, on_change=lambda e, s=s: self.page.run_task(self.toggle, s, e.control.value)),
                ft.Column([ft.Text(s.name, weight=ft.FontWeight.W_600, no_wrap=True),
                           ft.Text(self._t("enabled") if s.enabled else self._t("disabled"), size=11, opacity=0.7)], spacing=0, expand=True),
                ft.IconButton(ft.Icons.EDIT_NOTE, tooltip=self._t("Edit config"), width=44, height=44,
                              on_click=lambda _, s=s: self.page.run_task(self.edit_site, s))],
                vertical_alignment=ft.CrossAxisAlignment.CENTER))

    # ---- helpers ----
    def _show(self, title: str, body: str, error: bool = False) -> None:
        self.page.show_dialog(ft.AlertDialog(
                        title=ft.Text(title, color=ft.Colors.ERROR if error else None, no_wrap=True), scrollable=True,
            content=ft.Container(width=min(380, (page.width or 400) - 32), content=ft.Text(body or self._t("(no output)"), font_family="monospace", size=11,
                                                            selectable=True)),
            actions=[ft.TextButton(self._t("Close"), on_click=lambda _: self.page.pop_dialog())]))

    async def _root(self, cmd: str, timeout: float = 60):
        try:
            return await self.ctx.run_privileged(cmd, timeout)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return None

    async def install(self) -> None:
        if await run_install_flow(self.ctx, "nginx"):
            await self.load()

    # ---- service control (always tests first) ----
    async def test(self) -> None:
        r = await self._root(N.TEST_CMD)
        if r is not None:
            out = (r.stdout + r.stderr).strip()
            self._show(self._t("Configuration test: OK ✓") if r.ok else self._t("Configuration test FAILED"), out, error=not r.ok)

    async def reload(self) -> None:
        r = await self._root(N.reload_command(self.systemd))
        if r is None:
            return
        if r.ok:
            toast(self.page, self._t("Configuration OK — nginx reloaded."))
        else:
            self._show(self._t("Not reloaded: configuration test failed"), (r.stdout + r.stderr).strip(), error=True)
        await self.load()

    async def service(self, verb: str) -> None:
        if not self.systemd:
            toast(self.page, self._t("This server has no systemd; use the terminal to manage nginx."), error=True)
            return
        cmd = f"systemctl {verb} nginx"
        if not await self.ctx.gate_risk(self._t(verb.title() + " nginx?"), [cmd]):
            return
        full = (N.PATH + "nginx -t && " + cmd) if verb in ("start", "restart") else cmd
        r = await self._root(full)
        if r is not None and not r.ok:
            self._show(self._t(verb.title() + " failed"), (r.stdout + r.stderr).strip(), error=True)
        elif r is not None:
            toast(self.page, self._t("nginx: {verb} done").format(verb=verb))
        await self.load()

    async def ports(self) -> None:
        r = await self._root(N.PORTS_CMD, 20)
        if r is not None:
            self._show(self._t("Listening ports"), (r.stdout or r.stderr).strip())

    # ---- sites ----
    async def toggle(self, s: N.Site, enable: bool) -> None:
        """Change, test, and automatically undo if the config no longer passes `nginx -t`."""
        try:
            do, undo = N.toggle_commands(s, enable)
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        if not await self.ctx.gate_risk(self._t("Enable {name}?").format(name=s.name) if enable
                                         else self._t("Disable {name}?").format(name=s.name), [do]):
            await self.load()
            return
        if (r := await self._root(do)) is None or not r.ok:
            if r is not None:
                self._show(self._t("Couldn't change the site"), (r.stderr or r.stdout).strip(), error=True)
            await self.load()
            return
        t = await self._root(N.TEST_CMD)
        if t is not None and t.ok:
            if await self.ctx.ask_yes_no(self._t("Config test passed ✓"),
                                         self._t("Reload nginx now to apply enabling “{name}”?").format(name=s.name) if enable
                                         else self._t("Reload nginx now to apply disabling “{name}”?").format(name=s.name),
                                         self._t("Reload")):
                await self.reload()
        else:
            await self._root(undo)
            self._show(self._t("Change undone: configuration test failed"), ((t.stdout + t.stderr) if t else "").strip(), error=True)
        await self.load()

    async def _hook(self, path: str, prev: str | None, site: N.Site | None):
        async def after_save() -> None:
            t = await self._root(N.TEST_CMD)
            if t is None:
                return
            if not t.ok:
                if await self.ctx.ask_yes_no(self._t("Configuration test failed"),
                                             (t.stdout + t.stderr).strip()[-900:] + "\n\n" + self._t("Roll back to the previous version?"),
                                             self._t("Roll back")):
                    try:
                        if prev is None:
                            await self.ctx.run_privileged(f"rm -f -- {shlex.quote(path)}")
                        else:
                            await self.ctx.write_privileged(path, prev.encode())
                        toast(self.page, self._t("Rolled back on the server. Reopen the file to see the restored version."))
                    except TakError as e:
                        toast(self.page, e.message, error=True)
                    await self.load()
                return
            if prev is None and self.layout == "sites" and site is not None:
                if await self.ctx.ask_yes_no(self._t("Config OK ✓"), self._t("Enable this new site now?"), self._t("Enable")):
                    await self.load()
                    s = next((x for x in self.sites if x.name == site.name), site)
                    await self.toggle(s, True)
                    return
            if await self.ctx.ask_yes_no(self._t("Config OK ✓"), self._t("Reload nginx to apply your changes?"), self._t("Reload")):
                await self.reload()
            await self.load()
        return after_save

    async def edit_site(self, s: N.Site) -> None:
        try:
            prev = (await self.ctx.ssh.read_file(s.path)).decode("utf-8", "replace")
        except TakError:
            r = await self._root(f"cat -- {shlex.quote(s.path)}")
            if r is None or not r.ok:
                toast(self.page, self._t("Couldn't read that file."), error=True)
                return
            prev = r.stdout
        await self.ctx.editor.open_with(s.path, sudo_write=True, after_save=await self._hook(s.path, prev, s))

    async def new_site(self) -> None:
        page = self.page
        name = ft.TextField(label=self._t("File name"), hint_text="example.com", autofocus=True)
        server = ft.TextField(label=self._t("Server name (domain)"), hint_text="example.com")
        kind = ft.Dropdown(label=self._t("Template"), value="static",
                           options=[ft.DropdownOption(key=k, text=v) for k, v in N.TEMPLATES.items()])
        target = ft.TextField(label=self._t("Web root folder"), value="/var/www/example")

        def on_kind(e):
            target.label = self._t("Upstream URL") if e.control.value == "proxy" else self._t("Web root folder")
            target.value = "http://127.0.0.1:3000" if e.control.value == "proxy" else "/var/www/example"
            page.update()
        kind.on_select = on_kind

        async def go(_):
            try:
                path = N.site_path(self.layout, name.value.strip())
                text = N.render_template(kind.value, server.value or name.value, target.value)
                if await self.ctx.ssh.exists(path):
                    raise ValueError(self._t("A config with that name already exists."))
            except ValueError as e:
                toast(page, str(e), error=True)
                return
            except TakError as e:
                toast(page, e.message, error=True)
                return
            page.pop_dialog()
            site = N.Site(path.rsplit("/", 1)[-1], False, self.layout, path)
            await self.ctx.editor.open_with(path, text, sudo_write=True, after_save=await self._hook(path, None, site))

        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(self._t("New site")), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([name, server, kind, target,
                ft.Text(self._t("Opens in the editor. After saving, the config is tested and you can roll back if it fails."),
                        size=11, opacity=0.7)], tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton(self._t("Cancel"), on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton(self._t("Create"), on_click=go)]))

    # ---- certbot ----
    async def certbot(self) -> None:
        page = self.page
        info = self.ctx.sysinfo
        pm = P.detect(info.package_manager) if info else None
        if pm is None:
            toast(page, self._t("No supported package manager detected."), error=True)
            return
        domains = ft.TextField(label=self._t("Domains (comma separated)"), hint_text="example.com, www.example.com")
        email = ft.TextField(label=self._t("Email for expiry notices"), keyboard_type=ft.KeyboardType.EMAIL)
        installed = bool(self.state and self.state.certbot)
        note = ft.Text("", size=11, font_family="monospace", selectable=True)

        def collect():
            ds = [d.strip() for d in (domains.value or "").split(",") if d.strip()]
            return N.certbot_steps(pm, installed, ds, email.value or "")

        def preview(_=None):
            try:
                steps = collect()
                note.value = self._t("Commands that will run:\n") + "\n".join(
                    ("sudo " if s.sudo else "") + s.cmd for s in steps)
                note.color = None
            except ValueError as e:
                note.value, note.color = str(e), ft.Colors.ERROR
            page.update()
        domains.on_change = email.on_change = preview

        async def go(_):
            try:
                steps = collect()
            except ValueError as e:
                toast(page, str(e), error=True)
                return
            page.pop_dialog()
            if await run_in_sheet(self.ctx, self._t("Get HTTPS certificate"), steps,
                                  {"kind": "certbot", "name": "certbot", "command": "certbot --nginx", "risk": "writes"},
                                  hints=P.friendly_hint) is not None:
                await self.load()

        page.show_dialog(ft.AlertDialog(
                        title=ft.Text(self._t("HTTPS certificate (Let's Encrypt)")), scrollable=True,
            content=ft.Container(width=360, content=ft.Column([
                ft.Text(self._t("Your domains must already point to this server and be reachable on port 80.") +
                        ("" if installed else self._t(" certbot isn't installed and will be installed first.")), size=12),
                ft.Text(self._t("certbot will edit your nginx config to add HTTPS and redirect HTTP to it, then reload nginx."),
                        size=12), domains, email, note], tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton(self._t("Cancel"), on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton(self._t("Continue"), on_click=go)]))


def nginx_page(ctx: AppContext) -> ft.Control:
    v = NginxView(ctx)
    ctx.page.run_task(v.load)
    return v.control
