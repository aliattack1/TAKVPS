"""Docker manager: containers, images, volumes, networks, compose. Install flow when missing."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import docker as D
from services.i18n import translate
from services.runner import Step
from .. import theme
from ..components.common import (
    card, empty_state, error_state, loading_state, skeleton_list,
    status_pill, toast,
)
from ..components.installflow import run_install_flow
from ..components.livelog import open_live_tail
from ..components.runsheet import run_in_sheet
from ..context import AppContext

TABS = [("containers", "Containers"), ("images", "Images"), ("volumes", "Volumes"), ("networks", "Networks"),
        ("compose", "Compose")]
# Map container state → semantic state for the status_pill.
_STATE_TO_SEMANTIC = {
    "running": "ok", "paused": "warning", "restarting": "warning",
    "exited": "neutral", "created": "neutral", "dead": "error",
}


class DockerView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        self.state: D.DockerState | None = None
        self.sudo = False
        self.mode = "containers"
        self.data: dict[str, list] = {}
        self.stats: dict[str, dict] = {}
        self.show_stats = False
        self.loading = False
        self.error_msg: str | None = None
        self.seg = ft.SegmentedButton(selected=["containers"], on_change=self._on_mode,
                                      segments=[ft.Segment(value=k, label=ft.Text(self._t(v))) for k, v in TABS])
        self.stats_sw = ft.Switch(label=self._t("Show CPU / memory usage"), value=False, on_change=self._on_stats)
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=theme.SPACE_SM)
        self.menu = ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text(self._t("Pull image…")), icon=ft.Icons.CLOUD_DOWNLOAD,
                             on_click=lambda _: self.page.run_task(self.pull)),
            ft.PopupMenuItem(content=ft.Text(self._t("Prune stopped containers")), icon=ft.Icons.DELETE_SWEEP,
                             on_click=lambda _: self.page.run_task(self.prune, "containers")),
            ft.PopupMenuItem(content=ft.Text(self._t("Prune dangling images")), icon=ft.Icons.DELETE_SWEEP,
                             on_click=lambda _: self.page.run_task(self.prune, "images")),
            ft.PopupMenuItem(content=ft.Text(self._t("Prune unused volumes")), icon=ft.Icons.DELETE_FOREVER,
                             on_click=lambda _: self.page.run_task(self.prune, "volumes")),
            ft.PopupMenuItem(content=ft.Text(self._t("Prune everything unused")), icon=ft.Icons.DELETE_FOREVER,
                             on_click=lambda _: self.page.run_task(self.prune, "everything"))])
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text(self._t("Docker"), size=22, weight=ft.FontWeight.W_600, expand=True), self.menu,
                    ft.IconButton(ft.Icons.REFRESH, tooltip=self._t("Refresh"), width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.refresh))], scroll=ft.ScrollMode.AUTO),
            self.progress, self.seg, self.list], expand=True, spacing=theme.SPACE_SM))
        self.render()

    # ---- running docker ----
    async def dk(self, cmd: str, timeout: float = 60):
        if self.sudo:
            return await self.ctx.run_privileged(cmd, timeout)
        return await self.ctx.ssh.run(cmd, timeout=timeout)

    async def detect(self) -> None:
        if self.ctx.ssh.status.value != "connected":
            self.state = None
            self.render()
            return
        try:
            self.state = D.parse_detect((await self.ctx.ssh.run(D.DETECT_CMD, timeout=25)).stdout)
            self.sudo = self.state.daemon == "denied"
            if self.sudo:                                             # user isn't in the docker group
                r = await self.ctx.run_privileged("docker info --format '{{.ServerVersion}}' 2>&1 | head -n 1")
                if r is not None and r.stdout.strip()[:1].isdigit():
                    self.state.daemon = "running"
                elif r is None:
                    self.sudo = False
        except TakError as e:
            toast(self.page, e.message, error=True)
        await self.refresh()

    async def refresh(self) -> None:
        st = self.state
        if st is None or not st.installed or st.daemon not in ("running",):
            self.render()
            return
        cmds = {"containers": (D.PS_CMD, D.parse_containers), "images": (D.IMAGES_CMD, D.parse_images),
                "volumes": (D.VOLUMES_CMD, D.parse_volumes), "networks": (D.NETWORKS_CMD, D.parse_networks)}
        self.loading = True
        self.error_msg = None
        self.render()
        try:
            if self.mode in cmds:
                cmd, parse = cmds[self.mode]
                r = await self.dk(cmd)
                self.data[self.mode] = parse(r.stdout) if r else []
                if self.mode == "containers":
                    self.ctx.index["container"] = [c.name for c in self.data["containers"]]
                if self.mode == "containers" and self.show_stats and r:
                    s = await self.dk(D.STATS_CMD, 45)
                    self.stats = D.parse_stats(s.stdout) if s else {}
            elif self.mode == "compose":
                r = await self.dk(D.COMPOSE_LS_CMD)
                self.data["compose"] = D.parse_compose_ls(r.stdout) if r else []
        except TakError as e:
            self.error_msg = e.message
            toast(self.page, e.message, error=True)
        self.loading = False
        self.render()

    def _on_mode(self, e) -> None:
        self.mode = next(iter(e.control.selected))
        self.page.run_task(self.refresh)

    def _on_stats(self, e) -> None:
        self.show_stats = e.control.value
        self.page.run_task(self.refresh)

    # ---- rendering ----
    def render(self) -> None:
        self.progress.visible = self.loading
        st = self.state
        self.seg.visible = self.menu.visible = bool(st and st.installed and st.daemon == "running")
        if self.ctx.ssh.status.value != "connected":
            # Not connected: empty state with the cloud-off icon
            self.list.controls = [empty_state(
                ft.Icons.CLOUD_OFF, self._t("Not connected"),
                self._t("Connect to a server to see its Docker containers."))]
        elif self.error_msg:
            # Show the error state with a Retry button
            self.list.controls = [error_state(
                self.error_msg, retry=lambda: self.page.run_task(self.refresh))]
        elif st is None:
            # First-load skeleton list — gives the user a sense of "data is coming"
            self.list.controls = [skeleton_list(rows=4)]
        elif not st.installed:
            # Friendly install card with icon + body + CTA — modern onboarding pattern
            self.list.controls = [card(ft.Column([
                ft.Row([ft.Icon(ft.Icons.VIEW_IN_AR, size=theme.ICON_SIZE_LG,
                                 color=ft.Colors.PRIMARY),
                        ft.Text(self._t("Docker isn't installed on this server"),
                                weight=ft.FontWeight.W_600, expand=True)],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(self._t("Installing is optional. You'll see exactly what will change and confirm before anything runs."),
                        size=12, opacity=0.7),
                ft.FilledButton(self._t("Install Docker…"), icon=ft.Icons.DOWNLOAD,
                                on_click=lambda _: self.page.run_task(self.install)),
            ], spacing=theme.SPACE_SM))]
        elif st.daemon in ("stopped", "unknown"):
            # Daemon stopped — show the version + a Start button
            ver = st.version.replace("Docker version ", "")
            self.list.controls = [card(ft.Column([
                ft.Row([ft.Icon(ft.Icons.PAUSE_CIRCLE, size=theme.ICON_SIZE_LG,
                                 color=ft.Colors.AMBER),
                        ft.Text(f"Docker {ver} is installed but the daemon isn't running",
                                weight=ft.FontWeight.W_600, expand=True)],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.FilledButton(self._t("Start Docker"), icon=ft.Icons.PLAY_ARROW,
                                on_click=lambda _: self.page.run_task(self.start_daemon)),
            ], spacing=theme.SPACE_SM))]
        else:
            self.list.controls = self._tab_controls()
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _msg(self, t: str) -> ft.Control:
        return ft.Container(padding=24, content=ft.Text(t, opacity=0.7, text_align=ft.TextAlign.CENTER))

    def _card(self, controls: list) -> ft.Control:
        return card(ft.Column(controls, spacing=theme.SPACE_SM))

    def _item(self, label, icon, fn):
        return ft.PopupMenuItem(content=ft.Text(label), icon=icon, on_click=fn)

    def _tab_controls(self) -> list[ft.Control]:
        items = self.data.get(self.mode)
        head: list[ft.Control] = []
        if self.sudo:
            head.append(ft.Text(self._t("Docker runs via sudo (your user isn't in the docker group)."),
                                size=11, opacity=0.6))
        if self.mode == "containers":
            head.append(self.stats_sw)
        if self.loading:
            return head + [skeleton_list(rows=4)]
        if items is None:
            # First-load: show skeleton
            return head + [skeleton_list(rows=4)]
        builders = {"containers": self._container, "images": self._image, "volumes": self._volume,
                    "networks": self._network, "compose": self._project}
        if self.mode == "compose" and not self.state.compose:
            return head + [empty_state(
                ft.Icons.EXTENSION_OFF, self._t("Docker Compose isn't available"),
                self._t("Install Docker Compose on the server to manage multi-container projects."))]
        tiles = [builders[self.mode](x) for x in items]
        if not tiles:
            # Modern empty state per Docker tab — pick an icon that fits the tab
            tab_icons = {
                "containers": ft.Icons.VIEW_IN_AR,
                "images": ft.Icons.LAYERS,
                "volumes": ft.Icons.STORAGE,
                "networks": ft.Icons.LAN,
                "compose": ft.Icons.SCHEMA,
            }
            tab_msgs = {
                "containers": self._t("No containers found"),
                "images": self._t("No images found"),
                "volumes": self._t("No volumes found"),
                "networks": self._t("No networks found"),
                "compose": self._t("No Compose projects"),
            }
            tab_subs = {
                "containers": self._t("Pull an image and run a container to get started."),
                "images": self._t("Pull an image to get started."),
                "volumes": self._t("Volumes are created when containers need persistent storage."),
                "networks": self._t("Networks are created when containers need to talk to each other."),
                "compose": self._t("Run `docker compose up` from a project folder to see it here."),
            }
            return head + [empty_state(tab_icons.get(self.mode, ft.Icons.INBOX),
                                       tab_msgs.get(self.mode, self._t("No data.")),
                                       tab_subs.get(self.mode, ""))]
        return head + tiles

    def _row(self, title: str, subtitle: str, chips: list, menu: list, on_click=None, extra: str = "") -> ft.Control:
        col = [ft.Text(title, weight=ft.FontWeight.W_600, no_wrap=True),
               ft.Text(subtitle, size=11, opacity=0.7, no_wrap=True)]
        if extra:
            col.append(ft.Text(extra, size=11, opacity=0.8))
        if chips:
            col.append(ft.Row(chips, spacing=theme.SPACE_XS))
        return ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, ink=bool(on_click), on_click=on_click,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, content=ft.Row(
                                [ft.Column(col, spacing=2, expand=True), ft.PopupMenuButton(items=menu)],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def _chip(self, label: str, color) -> ft.Control:
        return ft.Container(ft.Text(label, size=10, color=ft.Colors.WHITE), bgcolor=color,
                            padding=ft.Padding.symmetric(horizontal=6, vertical=1), border_radius=4)

    def _container(self, c: D.Container) -> ft.Control:
        def act(v):
            return lambda _: self.page.run_task(self.act, c, v)
        s = self.stats.get(c.name)
        extra = f"CPU {s['cpu']} · Mem {s['mem']} · Net {s['net']}" if s else ""
        running = c.state == "running"
        menu = [self._item(self._t("Stop" if running else "Start"),
                           ft.Icons.STOP if running else ft.Icons.PLAY_ARROW,
                           act("stop" if running else "start")),
                self._item(self._t("Restart"), ft.Icons.RESTART_ALT, act("restart")),
                self._item(self._t("Logs (recent)"), ft.Icons.ARTICLE,
                           lambda _: self.page.run_task(self.logs, c)),
                self._item(self._t("Logs (live)"), ft.Icons.PODCASTS,
                           lambda _: self.page.run_task(open_live_tail, self.ctx,
                               f"{c.name} logs", D.logs_command(c.name, 50, True), self.sudo)),
                self._item(self._t("Inspect"), ft.Icons.INFO,
                           lambda _: self.page.run_task(self.inspect, c)),
                self._item(self._t("Open shell in terminal"), ft.Icons.TERMINAL,
                           lambda _: self.page.run_task(self.shell_into, c)),
                self._item(self._t("Kill"), ft.Icons.BOLT, act("kill")),
                self._item(self._t("Remove"), ft.Icons.DELETE, act("rm")),
                self._item(self._t("Force remove"), ft.Icons.DELETE_FOREVER, act("rmf"))]
        # Modern status pill instead of a tiny plain-color chip.
        state_semantic = _STATE_TO_SEMANTIC.get(c.state, "neutral")
        chips = [status_pill(c.state, state_semantic)]
        if c.ports:
            chips.append(ft.Text(c.ports, size=10, opacity=0.7, no_wrap=True))
        return self._row(c.name, f"{c.image} · {c.status}", chips, menu,
                         lambda _: self.page.run_task(self.logs, c), extra)

    def _image(self, im: D.Image) -> ft.Control:
        return self._row(im.ref, f"{im.size} · {im.created}", [], [
            self._item(self._t("Remove"), ft.Icons.DELETE,
                       lambda _: self.page.run_task(self.remove_generic, "image", im.ref))])

    def _volume(self, v: dict) -> ft.Control:
        return self._row(v["name"], v["driver"], [], [
            self._item(self._t("Remove"), ft.Icons.DELETE,
                       lambda _: self.page.run_task(self.remove_generic, "volume", v["name"]))])

    def _network(self, n: dict) -> ft.Control:
        builtin = n["name"] in D.BUILTIN_NETWORKS
        chips = [status_pill(self._t("built-in"), "neutral")] if builtin else []
        menu = [] if builtin else [self._item(self._t("Remove"), ft.Icons.DELETE,
                       lambda _: self.page.run_task(self.remove_generic, "network", n["name"]))]
        return self._row(n["name"], f"{n['driver']} · {n['scope']}", chips, menu)

    def _project(self, p: D.ComposeProject) -> ft.Control:
        def act(v):
            return lambda _: self.page.run_task(self.compose, p, v)
        return self._row(p.name, p.status, [], [
            self._item("Up (detached)", ft.Icons.PLAY_ARROW, act("up")), self._item("Down", ft.Icons.STOP, act("down")),
            self._item("Restart", ft.Icons.RESTART_ALT, act("restart")), self._item("Pull images", ft.Icons.CLOUD_DOWNLOAD, act("pull")),
            self._item("Status (ps)", ft.Icons.LIST, act("ps")), self._item("Logs", ft.Icons.ARTICLE, act("logs"))])

    # ---- actions ----
    def _show(self, title: str, body: str) -> None:
        self.page.show_dialog(ft.AlertDialog(
                        title=ft.Text(title, no_wrap=True), scrollable=True,
            content=ft.Container(width=min(380, (page.width or 400) - 32), content=ft.Text(body or "(empty)", font_family="monospace", size=11, selectable=True)),
            actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))

    async def install(self) -> None:
        if await run_install_flow(self.ctx, "docker"):
            await self.detect()

    async def start_daemon(self) -> None:
        r = await self.ctx.run_privileged("systemctl start docker")
        if r is not None and not r.ok:
            self._show("Couldn't start Docker", (r.stderr or r.stdout).strip())
        await self.detect()

    async def act(self, c: D.Container, verb: str) -> None:
        cmd = D.container_command(verb, c.name)
        if not await self.ctx.gate_risk(f"{verb} {c.name}?", [cmd]):
            return
        try:
            r = await self.dk(cmd)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        if r is not None and not r.ok:
            self._show(f"{verb} failed", (r.stderr or r.stdout).strip())
        elif r is not None:
            toast(self.page, f"{c.name}: {verb} done")
        await self.refresh()

    async def logs(self, c: D.Container) -> None:
        try:
            r = await self.dk(D.logs_command(c.name, 200))
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        self._show(f"{c.name} · recent logs", (r.stdout if r else "").strip())

    async def inspect(self, c: D.Container) -> None:
        r = await self.dk(D.inspect_command(c.name))
        self._show(f"{c.name} · inspect", D.pretty_inspect(r.stdout) if r else "")

    async def shell_into(self, c: D.Container) -> None:
        await self.ctx.terminal.ensure_session()
        if not (self.ctx.terminal.session and self.ctx.terminal.session.alive):
            toast(self.page, "Couldn't open a terminal session.", error=True)
            return
        self.ctx.terminal.send(("sudo " if self.sudo else "") + D.exec_command(c.name) + "\r")
        self.ctx.navigate("terminal")

    async def pull(self) -> None:
        name = await self.ctx.ask_text("Pull image", "Image (e.g. nginx:latest)", ok="Pull")
        if not name:
            return
        try:
            cmd = D.pull_command(name.strip())
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        await run_in_sheet(self.ctx, f"Pull {name.strip()}", [Step("docker pull", cmd, sudo=self.sudo)],
                           {"kind": "docker", "name": f"pull {name.strip()}", "command": cmd, "risk": "writes"})
        await self.refresh()

    async def remove_generic(self, kind: str, ref: str) -> None:
        cmd = {"image": D.image_rm_command, "volume": D.volume_rm_command, "network": D.network_rm_command}[kind](ref)
        if not await self.ctx.gate_risk(f"Remove {kind} {ref}?", [cmd], force_confirm=True):
            return
        try:
            r = await self.dk(cmd)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return
        if r is not None and not r.ok:
            self._show("Couldn't remove", (r.stderr or r.stdout).strip())
        await self.refresh()

    async def prune(self, what: str) -> None:
        cmd = D.PRUNES[what]
        warn = {"volumes": "This permanently deletes every volume not used by a container — including data.",
                "everything": "This deletes stopped containers, unused networks, ALL unused images and build cache."}
        if not await self.ctx.gate_risk(f"Prune {what}?", [cmd], force_confirm=True):
            return
        if what in warn and not await self.ctx.ask_typed("Are you sure?", warn[what], "prune"):
            return
        await run_in_sheet(self.ctx, f"Prune {what}", [Step("prune", cmd, sudo=self.sudo)],
                           {"kind": "docker", "name": f"prune {what}", "command": cmd, "risk": "destructive"})
        await self.refresh()

    async def compose(self, p: D.ComposeProject, verb: str) -> None:
        try:
            cmd = D.compose_command(p, verb)
        except ValueError as e:
            toast(self.page, str(e), error=True)
            return
        if not await self.ctx.gate_risk(f"{verb} {p.name}?", [cmd]):
            return
        if verb in ("ps", "logs"):
            r = await self.dk(cmd, 60)
            self._show(f"{p.name} · {verb}", ((r.stdout + r.stderr) if r else "").strip())
            return
        await run_in_sheet(self.ctx, f"compose {verb}: {p.name}", [Step(f"compose {verb}", cmd, sudo=self.sudo)],
                           {"kind": "docker", "name": f"compose {verb} {p.name}", "command": cmd, "risk": "writes"})
        await self.refresh()


def docker_page(ctx: AppContext) -> ft.Control:
    v = DockerView(ctx)
    ctx.page.run_task(v.detect)
    return v.control
