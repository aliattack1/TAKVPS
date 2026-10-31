"""Quick actions: reboot, shutdown, update, clear caches, free memory, vacuum journal. All confirm first."""
from __future__ import annotations

import flet as ft

from core.errors import TakError
from services import packages as P
from services.risk import classify
from services.runner import Step
from .. import theme
from ..components.common import toast
from ..components.runsheet import run_in_sheet
from ..context import AppContext

CLEAN = {"apt": "apt-get clean", "dnf": "dnf clean all", "yum": "yum clean all", "pacman": "pacman -Sc --noconfirm",
         "apk": "apk cache clean", "zypper": "zypper clean --all"}
DROP_CACHES = "sync; echo 3 > /proc/sys/vm/drop_caches"
VACUUM = "journalctl --vacuum-time=7d"


def _pm(ctx: AppContext):
    return P.detect(ctx.sysinfo.package_manager) if ctx.sysinfo else None


async def _connected(ctx: AppContext) -> bool:
    if ctx.ssh.status.value != "connected":
        toast(ctx.page, "Not connected.", error=True)
        return False
    return True


async def _power(ctx: AppContext, cmd: str, title: str) -> None:
    if not await _connected(ctx) or not await ctx.gate_risk(title, [cmd]):
        return
    try:
        await ctx.run_privileged(cmd, 15)
    except TakError:
        pass                                              # the connection drops as the server goes down
    toast(ctx.page, f"{title.rstrip('?')} command sent. Reconnect in a minute or two.")


async def reboot(ctx: AppContext) -> None:
    await _power(ctx, "reboot", "Reboot the server?")


async def shutdown(ctx: AppContext) -> None:
    await _power(ctx, "shutdown -h now", "Shut the server down? (You'll have to power it on from your provider.)")


async def _sheet(ctx: AppContext, title: str, cmd: str, confirm_text: str) -> None:
    if not await _connected(ctx):
        return
    risk = classify(cmd)
    if risk.level in ("destructive", "dangerous"):
        if not await ctx.gate_risk(title + "?", [cmd]):
            return
    elif not await ctx.ask_yes_no(title + "?", confirm_text + "\n\n" + cmd, "Run"):
        return
    await run_in_sheet(ctx, title, [Step(title, cmd, sudo=True)],
                       {"kind": "quick", "name": title, "command": cmd, "risk": risk.level}, hints=P.friendly_hint)


async def update_system(ctx: AppContext) -> None:
    pm = _pm(ctx)
    if pm is None:
        toast(ctx.page, "No supported package manager detected.", error=True)
        return
    await _sheet(ctx, "Update system", P.cmd_upgrade_all(pm), "Installs all available package updates.")


async def clear_caches(ctx: AppContext) -> None:
    pm = _pm(ctx)
    if pm is None:
        toast(ctx.page, "No supported package manager detected.", error=True)
        return
    await _sheet(ctx, "Clear package caches", CLEAN[pm], "Deletes downloaded package files (safe; they're re-downloaded if needed).")


async def free_memory(ctx: AppContext) -> None:
    await _sheet(ctx, "Free memory", DROP_CACHES, "Drops the filesystem cache. Harmless, but the server may be a little slower "
                 "while the cache refills.")


async def vacuum_journal(ctx: AppContext) -> None:
    await _sheet(ctx, "Vacuum system journal", VACUUM, "Deletes journal logs older than 7 days.")


ACTIONS = [
    ("Update system", ft.Icons.SYSTEM_UPDATE, "Install all package updates", update_system),
    ("Clear caches", ft.Icons.CLEANING_SERVICES, "Package manager caches", clear_caches),
    ("Free memory", ft.Icons.MEMORY, "Drop filesystem cache", free_memory),
    ("Vacuum journal", ft.Icons.DELETE_SWEEP, "Delete logs older than 7 days", vacuum_journal),
    ("Reboot", ft.Icons.RESTART_ALT, "Restart the server", reboot),
    ("Shut down", ft.Icons.POWER_SETTINGS_NEW, "Power the server off", shutdown),
]


def quick_row(ctx: AppContext) -> ft.Control:
    """Compact chips for the Overview page."""
    return ft.Row([ft.FilledTonalButton(label, icon=icon, height=40, disabled=ctx.ssh.status.value != "connected",
                                        on_click=lambda _, fn=fn: ctx.page.run_task(fn, ctx))
                   for label, icon, _d, fn in ACTIONS[:5]], wrap=True, spacing=6, run_spacing=6)


def quick_page(ctx: AppContext) -> ft.Control:
    tiles = [ft.Container(border_radius=theme.RADIUS, padding=theme.SPACE, ink=True, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                          on_click=lambda _, fn=fn: ctx.page.run_task(fn, ctx),
                          content=ft.Row([ft.Icon(icon, color=ft.Colors.ERROR if label in ("Reboot", "Shut down") else ft.Colors.PRIMARY),
                                          ft.Column([ft.Text(label, weight=ft.FontWeight.W_600), ft.Text(desc, size=11, opacity=0.7)],
                                                    spacing=0, expand=True), ft.Icon(ft.Icons.CHEVRON_RIGHT)]))
             for label, icon, desc, fn in ACTIONS]
    return ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
        ft.Text("Quick actions", size=22, weight=ft.FontWeight.W_600),
        ft.Text("Every action asks for confirmation first; power actions need a typed confirmation.", size=12, opacity=0.7), *tiles])
