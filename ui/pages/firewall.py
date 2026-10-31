"""Firewall manager: ufw and firewalld (full), iptables/nft (read-only). SSH access is always protected."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import firewall as F
from services.runner import Step
from .. import theme
from ..components.common import toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext

_ACT = {"ALLOW": ft.Colors.GREEN, "LIMIT": ft.Colors.AMBER_700, "DENY": ft.Colors.RED, "REJECT": ft.Colors.RED}


class FirewallView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.backend = ""
        self.ufw = F.UfwStatus()
        self.fwd = F.FirewalldInfo()
        self.raw = ""
        self.msg = "Loading…"
        self.progress = ft.ProgressBar(visible=False)
        self.list = ft.ListView(expand=True, spacing=8)
        self.control = ft.Container(padding=theme.SPACE, expand=True, content=ft.Column([
            ft.Row([ft.Text("Firewall", size=22, weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip="Refresh", width=44, height=44,
                                  on_click=lambda _: self.page.run_task(self.load))], scroll=ft.ScrollMode.AUTO),
            self.progress, self.list], expand=True, spacing=8))
        self.render()

    @property
    def ssh_port(self) -> int:
        return self.ctx.active.port if self.ctx.active else 22

    # ---- data ----
    async def load(self) -> None:
        ctx = self.ctx
        if ctx.ssh.status.value != "connected":
            self.backend, self.msg = "", "Not connected."
            self.render()
            return
        self.progress.visible = True
        self.page.update()
        try:
            self.backend = F.detect_backend((await ctx.ssh.run(F.DETECT_CMD, timeout=15)).stdout)
            if not self.backend:
                self.msg = "No firewall tool found (ufw, firewalld, nftables or iptables)."
            elif self.backend == "ufw":
                r = await ctx.run_privileged(F.UFW_STATUS)
                self.ufw = F.parse_ufw(r.stdout) if r else F.UfwStatus()
                self.msg = "" if r else "Administrator access is needed to read the firewall."
            elif self.backend == "firewalld":
                s = await ctx.run_privileged(F.FIREWALLD_STATE)
                l = await ctx.run_privileged(F.FIREWALLD_LIST) if s else None
                self.fwd = F.parse_firewalld(s.stdout if s else "", l.stdout if l else "")
                self.msg = "" if s else "Administrator access is needed to read the firewall."
            else:
                r = await ctx.run_privileged(F.NFT_LIST if self.backend == "nft" else F.IPTABLES_LIST)
                self.raw = (r.stdout + r.stderr).strip() if r else ""
                self.msg = ""
        except TakError as e:
            self.msg = e.message
        self.progress.visible = False
        self.render()

    # ---- rendering ----
    def _card(self, controls: list) -> ft.Control:
        return ft.Container(padding=theme.SPACE, border_radius=theme.RADIUS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                            content=ft.Column(controls, spacing=8))

    def render(self) -> None:
        out: list[ft.Control] = []
        if self.msg and not self.backend:
            out.append(ft.Container(padding=24, content=ft.Text(self.msg, opacity=0.7)))
        elif self.backend:
            out.append(ft.Container(padding=10, border_radius=8, bgcolor=ft.Colors.SECONDARY_CONTAINER, content=ft.Row([
                ft.Icon(ft.Icons.SHIELD, size=18),
                ft.Text(f"SSH port {self.ssh_port} is protected: this app refuses changes that would close it.",
                        size=12, expand=True)])))
            out += {"ufw": self._ufw_ui, "firewalld": self._fwd_ui}.get(self.backend, self._raw_ui)()
        self.list.controls = out
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _ufw_ui(self) -> list[ft.Control]:
        st = self.ufw
        head = self._card([
            ft.Row([ft.Icon(ft.Icons.CIRCLE, size=12, color=ft.Colors.GREEN if st.active else ft.Colors.OUTLINE),
                    ft.Text("ufw is " + ("active" if st.active else "inactive"), weight=ft.FontWeight.W_600, expand=True)]),
            ft.Row([ft.FilledTonalButton("Disable" if st.active else "Enable", icon=ft.Icons.POWER_SETTINGS_NEW, height=40,
                                         on_click=lambda _: self.page.run_task(self.ufw_toggle)),
                    ft.FilledButton("Add rule", icon=ft.Icons.ADD, height=40,
                                    on_click=lambda _: self.page.run_task(self.add_rule))], spacing=6)])
        rows = []
        for r in st.rules:
            rows.append(ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                                     content=ft.Row([
                ft.Container(ft.Text(r.action, size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.W_700),
                             bgcolor=_ACT[r.action], padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=4),
                ft.Column([ft.Text(r.to, weight=ft.FontWeight.W_600, no_wrap=True),
                           ft.Text(f"{r.direction.lower()} from {r.frm}", size=11, opacity=0.7, no_wrap=True)], spacing=0, expand=True),
                ft.IconButton(ft.Icons.DELETE, tooltip="Delete rule", width=44, height=44,
                              on_click=lambda _, n=r.num: self.page.run_task(self.ufw_delete, n))],
                vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        return [head] + (rows or [ft.Text("No rules.", opacity=0.7)])

    def _fwd_ui(self) -> list[ft.Control]:
        i = self.fwd
        head = self._card([ft.Row([ft.Icon(ft.Icons.CIRCLE, size=12, color=ft.Colors.GREEN if i.running else ft.Colors.OUTLINE),
                                   ft.Text(f"firewalld is {'running' if i.running else 'not running'}"
                                           + (f" · zone {i.zone}" if i.zone else ""), weight=ft.FontWeight.W_600, expand=True)]),
                           ft.FilledButton("Add port / service", icon=ft.Icons.ADD, height=40,
                                           on_click=lambda _: self.page.run_task(self.add_rule))])
        rows = [self._fwd_row("service", s) for s in i.services] + [self._fwd_row("port", p) for p in i.ports]
        return [head] + (rows or [ft.Text("Nothing opened.", opacity=0.7)])

    def _fwd_row(self, kind: str, value: str) -> ft.Control:
        return ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                            content=ft.Row([ft.Icon(ft.Icons.LAN if kind == "port" else ft.Icons.SETTINGS_SUGGEST),
                                            ft.Text(f"{value}  ({kind})", expand=True),
                                            ft.IconButton(ft.Icons.DELETE, width=44, height=44,
                                                          on_click=lambda _: self.page.run_task(self.fwd_remove, kind, value))]))

    def _raw_ui(self) -> list[ft.Control]:
        return [self._card([ft.Text(f"{self.backend} rules (read-only)", weight=ft.FontWeight.W_600),
                            ft.Text("Changing raw iptables/nftables rules from a phone is easy to get wrong, so this app only "
                                    "shows them. Use the terminal to change them, or install ufw/firewalld for guided editing.",
                                    size=12, opacity=0.7),
                            ft.Text(self.raw or "(empty)", font_family="monospace", size=10, selectable=True)])]

    # ---- actions ----
    def _err(self, title: str, body: str) -> None:
        self.page.show_dialog(ft.AlertDialog(title=ft.Text(title, color=ft.Colors.ERROR), scrollable=True,
                                             content=ft.Text(body, font_family="monospace", size=11, selectable=True),
                                             actions=[ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]))

    async def _apply(self, title: str, cmds: list[str], confirm_text: str) -> None:
        if not await self.ctx.ask_yes_no(title, confirm_text + "\n\n" + "\n".join(cmds), "Apply"):
            return
        rep = await run_in_sheet(self.ctx, title, [Step(c.split()[0] + " …", c, sudo=True) for c in cmds],
                                 {"kind": "firewall", "name": title, "command": cmds[-1], "risk": "dangerous"})
        if rep is not None:
            await self.load()

    async def ufw_toggle(self) -> None:
        if self.ufw.active:
            await self._apply("Disable the firewall", ["ufw disable"], "All ufw rules stop being enforced.")
        else:
            cmds = F.ufw_enable_plan(self.ufw, self.ssh_port)
            extra = f"Port {self.ssh_port}/tcp will be allowed first so SSH keeps working. " if len(cmds) > 1 else ""
            await self._apply("Enable the firewall", cmds, extra + "Incoming connections not matched by a rule are blocked.")

    async def ufw_delete(self, num: int) -> None:
        if (msg := F.ufw_delete_guard(self.ufw, num, self.ssh_port)):
            self._err("Can't delete this rule", msg)
            return
        await self._apply("Delete rule", [F.ufw_delete_command(num)], f"Delete rule {num}.")

    async def fwd_remove(self, kind: str, value: str) -> None:
        try:
            cmd = F.firewalld_command("remove", kind, value, self.ssh_port)
        except ValueError as e:
            self._err("Can't remove this", str(e))
            return
        await self._apply("Remove rule", [cmd], f"Remove {value}.")

    async def add_rule(self) -> None:
        page = self.page
        if self.backend == "ufw":
            act = ft.Dropdown(label="Action", value="allow", options=[ft.DropdownOption(key=k) for k in ("allow", "deny", "reject", "limit")])
            port = ft.TextField(label="Port or range", hint_text="8080 or 6000:6100", keyboard_type=ft.KeyboardType.NUMBER)
            proto = ft.Dropdown(label="Protocol", value="tcp", options=[ft.DropdownOption(key=k) for k in ("tcp", "udp", "any")])
            src = ft.TextField(label="Only from (optional)", hint_text="203.0.113.5 or 10.0.0.0/24")
            fields = [act, port, proto, src]

            def build() -> str:
                return F.ufw_add_command(act.value, port.value or "", proto.value, src.value or "", self.ssh_port)
        else:
            kind = ft.Dropdown(label="Type", value="port", options=[ft.DropdownOption(key="port"), ft.DropdownOption(key="service")])
            val = ft.TextField(label="Value", hint_text="8080/tcp, 6000-6100/udp or http")
            fields = [kind, val]

            def build() -> str:
                return F.firewalld_command("add", kind.value, (val.value or "").strip(), self.ssh_port)

        async def go(_):
            try:
                cmd = build()
            except ValueError as e:
                toast(page, str(e), error=True)
                return
            page.pop_dialog()
            await self._apply("Add firewall rule", [cmd], "Add this rule.")

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Add rule"), scrollable=True,
            content=ft.Container(width=340, content=ft.Column(fields, tight=True, spacing=theme.SPACE)),
            actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()), ft.FilledButton("Continue", on_click=go)]))


def firewall_page(ctx: AppContext) -> ft.Control:
    v = FirewallView(ctx)
    ctx.page.run_task(v.load)
    return v.control
