"""compare a file between two servers.

Opens a file on the active server and on another saved server, then shows
a side-by-side or unified diff. Uses `diff` on the active server to compare
the two files (the second file is fetched via a temporary SSH connection).
"""
from __future__ import annotations

import difflib
from typing import Optional

import flet as ft

from core.errors import TakError
from core.events import EventBus
from core.ssh_client import SSHClient
from services.i18n import translate
from .. import theme
from ..components.common import toast
from ..context import AppContext


async def open_diff_dialog(ctx: AppContext) -> None:
    """Show the diff dialog: pick a remote file path and a target server to compare with."""
    page = ctx.page
    lang = ctx.store.data.language
    _t = lambda s: translate(s, lang)  # noqa: E731
    if ctx.ssh.status.value != "connected":
        toast(page, _t("Connect to a server first."), error=True)
        return
    others = [p for p in ctx.conns.list() if p.id != (ctx.active.id if ctx.active else "")]
    if not others:
        toast(page, _t("Add a second server to compare with."), error=True)
        return

    path_field = ft.TextField(label=_t("File path on active server"), value="/etc/nginx/nginx.conf",
                               dense=True, expand=True, border_radius=8)
    other_dd = ft.Dropdown(label=_t("Compare with server"), expand=True,
                           options=[ft.DropdownOption(key=p.id, text=p.name) for p in others])
    result = ft.Text("", size=11, font_family="monospace", selectable=True, no_wrap=False)

    async def do_diff(_=None) -> None:
        path = (path_field.value or "").strip()
        other_id = other_dd.value
        if not path or not other_id:
            toast(page, _t("Enter a file path and pick a server."), error=True)
            return
        other = ctx.conns.get(other_id)
        if other is None:
            toast(page, _t("Server not found."), error=True)
            return
        result.value = _t("Fetching files…")
        page.update()
        # Read the file from the active server.
        try:
            text_a = (await ctx.ssh.run(f"cat {path} 2>/dev/null", timeout=10)).stdout
        except TakError as e:
            result.value = f"Active server: {e.message}"
            page.update()
            return
        # Read the same file from the other server via a throwaway SSH connection.
        try:
            creds = ctx.conns.credentials(other, ctx.vault)
        except Exception as e:
            result.value = f"Can't read credentials for {other.name}: {e}"
            page.update()
            return

        async def deny(host, fp, changed):
            return False

        tmp = SSHClient(EventBus(), ctx.store.data.known_hosts, deny)
        text_b = ""
        try:
            await tmp.connect(creds)
            r = await tmp.run(f"cat {path} 2>/dev/null", timeout=10)
            text_b = r.stdout
        except Exception as e:
            result.value = f"{other.name}: {getattr(e, 'message', None) or e}"
            page.update()
            return
        finally:
            try:
                await tmp.disconnect()
            except Exception:
                pass
        # Compute unified diff.
        lines_a = text_a.splitlines(keepends=True)
        lines_b = text_b.splitlines(keepends=True)
        diff = list(difflib.unified_diff(lines_a, lines_b,
                                          fromfile=f"{ctx.active.name}:{path}" if ctx.active else path,
                                          tofile=f"{other.name}:{path}", n=3))
        if not diff:
            result.value = _t("✓ Files are identical.")
        else:
            # Colorize the diff lines.
            colored_parts = []
            for ln in diff:
                if ln.startswith("+") and not ln.startswith("+++"):
                    colored_parts.append(("add", ln))
                elif ln.startswith("-") and not ln.startswith("---"):
                    colored_parts.append(("del", ln))
                elif ln.startswith("@@"):
                    colored_parts.append(("hunk", ln))
                else:
                    colored_parts.append(("ctx", ln))
            # Render as spans in a Text control.
            spans = []
            for kind, text in colored_parts:
                color = {"add": ft.Colors.GREEN, "del": ft.Colors.RED,
                         "hunk": ft.Colors.BLUE, "ctx": ft.Colors.ON_SURFACE_VARIANT}[kind]
                spans.append(ft.TextSpan(text, ft.TextStyle(color=color)))
            result_text = ft.Text(spans=spans, font_family="monospace", size=10,
                                   selectable=True, no_wrap=False)
            # Replace the result control.
            for i, c in enumerate(page.controls if hasattr(page, "controls") else []):
                pass
            # Just update the result text directly.
            result.value = ""
            result.spans = spans
        page.update()

    page.show_dialog(ft.AlertDialog(
                title=ft.Text(_t("Compare file between servers")), modal=True, scrollable=True,
        content=ft.Container(width=min(440, (page.width or 400) - 32), content=ft.Column([
            ft.Text(_t("Compare a file on the active server with the same path on another server."),
                     size=12, opacity=0.7),
            path_field,
            other_dd,
            ft.FilledButton(_t("Compare"), icon=ft.Icons.COMPARE_ARROWS, on_click=lambda _: page.run_task(do_diff)),
            ft.Container(
                content=result, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                padding=8, border_radius=8, height=300,
            ),
        ], tight=True, scroll=ft.ScrollMode.AUTO, spacing=8)),
        actions=[ft.TextButton(_t("Close"), on_click=lambda _: page.pop_dialog())]))
