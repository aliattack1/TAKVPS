"""Preflight → plan → explicit confirmation → live install → verification. Nothing runs before Confirm."""
from __future__ import annotations

import asyncio
from typing import Optional

import flet as ft

from core.errors import TakError
from services import installplan as IP
from services import packages as P
from .. import theme
from .common import toast
from .runsheet import run_in_sheet

_ICON = {"ok": (ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN), "warn": (ft.Icons.WARNING_AMBER, ft.Colors.AMBER),
         "fail": (ft.Icons.CANCEL, ft.Colors.RED)}


async def run_install_flow(ctx, kind: str) -> bool:
    """kind: "docker" | "nginx". Returns True if the install ran and succeeded."""
    page = ctx.page
    if ctx.ssh.status.value != "connected":
        toast(page, "Not connected.", error=True)
        return False
    title = "Docker" if kind == "docker" else "Nginx"

    page.show_dialog(ft.AlertDialog(modal=True, title=ft.Text(f"Checking this server for {title}…"),
                                    content=ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=2),
                                                    ft.Text("Read-only checks only.")], tight=True)))
    try:
        r = await ctx.ssh.run(IP.PREFLIGHT_CMD, timeout=40)
    except TakError as e:
        page.pop_dialog()
        toast(page, e.message, error=True)
        return False
    page.pop_dialog()

    pf = IP.parse_preflight(r.stdout)
    info = ctx.sysinfo
    pm = P.detect(info.package_manager) if info else None
    sudo = info.sudo if info else "none"
    user = ctx.active.username if ctx.active else ""
    opts = {"hello": False, "group": False}

    def plan_now() -> IP.Plan:
        return IP.build_plan(kind, pf, pm, sudo, bool(info and info.has_systemd), user, opts)

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    body = ft.Column(tight=True, spacing=theme.SPACE, scroll=ft.ScrollMode.AUTO)
    ok_btn = ft.FilledButton("Install")

    def done(v: bool) -> None:
        page.pop_dialog()
        if not fut.done():
            fut.set_result(v)

    def draw() -> None:
        plan = plan_now()
        rows: list[ft.Control] = [ft.Text("Preflight checks", weight=ft.FontWeight.W_600)]
        for c in plan.checks:
            icon, color = _ICON[c.status]
            rows.append(ft.Row([ft.Icon(icon, color=color, size=18),
                                ft.Column([ft.Text(c.name, size=13, weight=ft.FontWeight.W_500),
                                           ft.Text(c.detail, size=11, opacity=0.7)], spacing=0, expand=True)],
                               vertical_alignment=ft.CrossAxisAlignment.START))
        if plan.blocked:
            rows.append(ft.Container(padding=10, border_radius=8, bgcolor=ft.Colors.ERROR_CONTAINER, content=ft.Text(
                "Installation can't continue: " + "; ".join(c.name for c in plan.blockers) + ". Nothing was changed.")))
        else:
            rows.append(ft.Divider())
            rows.append(ft.Text("What will happen", weight=ft.FontWeight.W_600))
            rows += [ft.Text("• " + c, size=12) for c in plan.changes]
            rows.append(ft.Text(f"Estimated disk use: {plan.est_disk}", size=12))
            if kind == "docker":
                rows.append(ft.Checkbox(label="Also run the hello-world test container", value=opts["hello"],
                                        on_change=lambda e: (opts.__setitem__("hello", e.control.value), draw(), page.update())))
                rows.append(ft.Checkbox(label=f"Add “{user}” to the docker group (optional)", value=opts["group"],
                                        on_change=lambda e: (opts.__setitem__("group", e.control.value), draw(), page.update())))
            rows += [ft.Text("⚠ " + w, size=12, color=ft.Colors.ERROR) for w in plan.warnings]
            rows.append(ft.ExpansionTile(title=ft.Text("Commands that will run", size=13), controls=[
                ft.Container(padding=10, content=ft.Text(
                    "\n".join(("sudo " if s.sudo and sudo != "root" else "") + s.cmd for s in plan.steps),
                    font_family="monospace", size=10, selectable=True))]))
        body.controls = rows
        ok_btn.disabled = plan.blocked

    ok_btn.on_click = lambda _: done(True)
    draw()
    page.show_dialog(ft.AlertDialog(
        modal=True, title=ft.Text(f"Install {title}?"), scrollable=True,
        content=ft.Container(width=360, content=body),
        actions=[ft.TextButton("Cancel", on_click=lambda _: done(False)), ok_btn]))
    if not await fut:
        return False

    plan = plan_now()
    rep = await run_in_sheet(ctx, plan.title, plan.steps, {"kind": "install", "name": plan.title,
                                                         "command": f"install {kind}", "risk": "writes"},
                             hints=P.friendly_hint)
    return bool(rep and rep.ok)
