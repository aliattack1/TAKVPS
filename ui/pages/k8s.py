"""Kubernetes manager page."""
from __future__ import annotations
import flet as ft
from core.errors import TakError
from services import k8s as K8S
from services.i18n import translate
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext


class K8sView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        # Persian translation helper bound to the user's language.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        _t = self._t
        self.pods: list[K8S.K8sPod] = []
        self.deployments: list[K8S.K8sDeployment] = []
        self.services: list[K8S.K8sService] = []
        self.busy = False
        self.progress = ft.ProgressBar(visible=False)
        self.seg = ft.SegmentedButton(
            selected=["pods"], on_change=lambda e: self.render(),
            segments=[ft.Segment(value="pods", label=ft.Text(_t("Pods"))),
                      ft.Segment(value="deployments", label=ft.Text(_t("Deployments"))),
                      ft.Segment(value="services", label=ft.Text(_t("Services")))])
        self.list = ft.ListView(expand=True, spacing=6)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text(_t("Kubernetes"), size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip=_t("Refresh"), width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.load))],
                   scroll=ft.ScrollMode.AUTO),
            self.progress, self.seg, self.list], expand=True, spacing=8))

    async def load(self) -> None:
        _t = self._t
        if self.ctx.ssh.status.value != "connected":
            self.list.controls = [ft.Text(_t("Not connected."), opacity=0.7)]
            return
        self.busy = True
        self.progress.visible = True
        self.page.update()
        # Detect kubectl
        try:
            r = await self.ctx.ssh.run(K8S.detect_command(), timeout=10)
            if not r.stdout.strip():
                self.list.controls = [ft.Column([
                    ft.Icon(ft.Icons.CLOUD_OFF, size=36, opacity=0.4),
                    ft.Text(_t("kubectl isn't installed or no cluster is connected."), weight=ft.FontWeight.W_600),
                    ft.Text(_t("Install kubectl and configure ~/.kube/config to use this page."), size=12, opacity=0.7, selectable=True),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)]
                self.busy = False
                self.progress.visible = False
                self.page.update()
                return
        except TakError as e:
            self.list.controls = [ft.Text(e.message, color=ft.Colors.ERROR)]
            self.busy = False
            self.progress.visible = False
            self.page.update()
            return
        # Load pods, deployments, services
        try:
            rp = await self.ctx.ssh.run(K8S.list_pods_command(), timeout=15)
            self.pods = K8S.parse_pods(rp.stdout)
        except TakError:
            self.pods = []
        try:
            rd = await self.ctx.ssh.run(K8S.list_deployments_command(), timeout=15)
            self.deployments = K8S.parse_deployments(rd.stdout)
        except TakError:
            self.deployments = []
        try:
            rs = await self.ctx.ssh.run(K8S.list_services_command(), timeout=15)
            self.services = K8S.parse_services(rs.stdout)
        except TakError:
            self.services = []
        self.busy = False
        self.progress.visible = False
        self.render()

    def render(self) -> None:
        _t = self._t
        tab = next(iter(self.seg.selected)) if self.seg.selected else "pods"
        cols: list[ft.Control] = []
        if tab == "pods":
            if not self.pods:
                cols.append(ft.Text(_t("No pods found."), opacity=0.7))
            for p in self.pods:
                ok = p.status == "Running"
                cols.append(ft.Container(
                    border_radius=theme.RADIUS, padding=theme.SPACE,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                    content=ft.Row([
                        ft.Icon(ft.Icons.CIRCLE, size=10, color=ft.Colors.GREEN if ok else ft.Colors.RED),
                        ft.Column([
                            ft.Text(p.name, weight=ft.FontWeight.W_500, no_wrap=True, size=12),
                            ft.Text(_t("{ready} · {status} · {restarts} restarts · {namespace}").format(
                                ready=p.ready, status=p.status, restarts=p.restarts, namespace=p.namespace),
                                    size=10, opacity=0.7, no_wrap=True),
                        ], spacing=1, expand=True),
                        ft.IconButton(ft.Icons.ARTICLE, tooltip=_t("View logs"), width=36, height=36,
                                      on_click=lambda _, p=p: self.page.run_task(self._logs, p)),
                        ft.IconButton(ft.Icons.TERMINAL, tooltip=_t("Exec shell"), width=36, height=36,
                                      on_click=lambda _, p=p: self._exec(p)),
                    ])))
        elif tab == "deployments":
            if not self.deployments:
                cols.append(ft.Text(_t("No deployments found."), opacity=0.7))
            for d in self.deployments:
                cols.append(ft.Container(
                    border_radius=theme.RADIUS, padding=theme.SPACE,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                    content=ft.Column([
                        ft.Text(d.name, weight=ft.FontWeight.W_500, size=12),
                        ft.Text(_t("Ready: {ready} · Up-to-date: {upd} · Available: {avail} · {namespace}").format(
                            ready=d.ready, upd=d.up_to_date, avail=d.available, namespace=d.namespace),
                                size=10, opacity=0.7),
                    ], spacing=2)))
        elif tab == "services":
            if not self.services:
                cols.append(ft.Text(_t("No services found."), opacity=0.7))
            for s in self.services:
                cols.append(ft.Container(
                    border_radius=theme.RADIUS, padding=theme.SPACE,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                    content=ft.Column([
                        ft.Text(s.name, weight=ft.FontWeight.W_500, size=12),
                        ft.Text(f"{s.type} · {s.cluster_ip} · {s.ports} · {s.namespace}",
                                size=10, opacity=0.7),
                    ], spacing=2)))
        self.list.controls = cols
        self.page.update()

    async def _logs(self, pod: K8S.K8sPod) -> None:
        """Show pod logs in a dialog."""
        _t = self._t
        try:
            r = await self.ctx.ssh.run(K8S.logs_command(pod.name, pod.namespace, tail=200), timeout=15)
            text = r.stdout or "(no logs)"
        except TakError as e:
            text = e.message
        self.page.show_dialog(ft.AlertDialog(
                        title=ft.Text(_t("Logs: {name}").format(name=pod.name)), modal=True, scrollable=True,
            content=ft.Container(width=min(420, (page.width or 400) - 32), height=min(400, (page.height or 600) - 200), content=ft.Column([
                ft.Text(text, font_family="monospace", size=10, selectable=True, no_wrap=False),
            ], scroll=ft.ScrollMode.AUTO, tight=True)),
            actions=[ft.TextButton(_t("Close"), on_click=lambda _: self.page.pop_dialog())]))

    def _exec(self, pod: K8s.K8sPod) -> None:
        """Open a terminal and exec into the pod."""
        _t = self._t
        cmd = K8S.exec_command(pod.name, pod.namespace, "/bin/sh")
        self.ctx.terminal.ensure_session()
        if self.ctx.terminal.session and self.ctx.terminal.session.alive:
            self.ctx.terminal.send(cmd.replace("kubectl exec -it", "kubectl exec") + "\r")
            self.ctx.navigate("terminal")
        else:
            toast(self.page, _t("Couldn't open a terminal session."), error=True)


def k8s_page(ctx: AppContext) -> ft.Control:
    v = K8sView(ctx)
    ctx.page.run_task(v.load)
    return v.control
