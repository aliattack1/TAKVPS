"""Connection manager: list, add/edit, test, duplicate, delete, import/export."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import flet as ft

from core.errors import TakError
from core.fmt import time_ago
from core.ssh_client import Status
from storage.connections import ConnectionConfigError, ConnectionProfile, Secrets
from storage.vault import VaultError
from .. import theme
from ..components.common import confirm, empty_state, toast
from ..context import AppContext

COLORS = ["#3B82F6", "#22C55E", "#EF4444", "#F59E0B", "#A855F7", "#14B8A6"]


def connections_page(ctx: AppContext) -> ft.Control:
    page = ctx.page
    profiles = ctx.conns.list()

    def is_active(p: ConnectionProfile) -> bool:
        return bool(ctx.active and ctx.active.id == p.id and ctx.ssh.status is Status.CONNECTED)

    async def open_profile(p: ConnectionProfile):
        if is_active(p) or await ctx.connect(p):
            ctx.navigate("overview")

    def menu(p: ConnectionProfile) -> ft.Control:
        async def dup(_):
            ctx.conns.duplicate(p.id)
            ctx.refresh_ui()

        def delete(_):
            def do():
                ctx.conns.delete(p.id)
                ctx.refresh_ui()
            confirm(page, "Delete server?", f"'{p.name}' and its saved credentials will be removed.",
                    do, yes_label="Delete")

        return ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text("Edit"), icon=ft.Icons.EDIT,
                             on_click=lambda _: open_editor(ctx, p)),
            ft.PopupMenuItem(content=ft.Text("Duplicate"), icon=ft.Icons.CONTENT_COPY, on_click=dup),
            ft.PopupMenuItem(content=ft.Text("Delete"), icon=ft.Icons.DELETE, on_click=delete)])

    def tile(p: ConnectionProfile) -> ft.Control:
        sub = [ft.Text(p.target, size=12, opacity=0.7)]
        if p.tags:
            sub.append(ft.Text(" · ".join(p.tags), size=11, opacity=0.6))
        sub.append(ft.Text("Connected" if is_active(p) else f"Last connected: {time_ago(p.last_connected)}",
                           size=11, opacity=0.6,
                           color=ft.Colors.GREEN if is_active(p) else None))

        async def tap(_):
            await open_profile(p)

        return ft.Container(
            border_radius=theme.RADIUS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            padding=theme.SPACE, on_click=tap, ink=True, height=None,
            content=ft.Row([
                ft.Container(width=6, height=56, border_radius=3, bgcolor=p.color),
                ft.Column([ft.Text(p.name, size=16, weight=ft.FontWeight.W_600), *sub],
                          spacing=2, expand=True),
                ft.Icon(ft.Icons.CIRCLE, size=12,
                        color=ft.Colors.GREEN if is_active(p) else ft.Colors.OUTLINE),
                menu(p)], vertical_alignment=ft.CrossAxisAlignment.CENTER))

    top = ft.Row([
        ft.Text("Servers", size=22, weight=ft.FontWeight.W_600, expand=True),
        ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text("Import connections"), icon=ft.Icons.UPLOAD,
                             on_click=lambda _: open_import(ctx)),
            ft.PopupMenuItem(content=ft.Text("Export connections"), icon=ft.Icons.DOWNLOAD,
                             on_click=lambda _: page.run_task(do_export, ctx))]),
        ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=lambda _: open_editor(ctx, None))], scroll=ft.ScrollMode.AUTO)

    # quick-connect recent servers bar.
    recent = [p for p in profiles if p.last_connected > 0]
    recent.sort(key=lambda p: p.last_connected, reverse=True)
    recent_bar = ft.Container(visible=False, padding=ft.Padding.symmetric(horizontal=theme.SPACE, vertical=2),
                              bgcolor=ft.Colors.TERTIARY_CONTAINER)
    if recent:
        chips: list[ft.Control] = [ft.Icon(ft.Icons.HISTORY, size=14, opacity=0.7)]
        for p in recent[:8]:
            chips.append(ft.Container(
                ink=True, on_click=lambda _, p=p: page.run_task(_quick_connect, ctx, p),
                padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                border_radius=14, bgcolor=ft.Colors.PRIMARY_CONTAINER,
                content=ft.Row([
                    ft.Icon(ft.Icons.CIRCLE, size=8,
                            color=ft.Colors.GREEN if is_active(p) else ft.Colors.OUTLINE),
                    ft.Text(p.name, size=11, no_wrap=True),
                ], spacing=4)))
        recent_bar.visible = True
        recent_bar.content = ft.Row(chips, spacing=6, scroll=ft.ScrollMode.AUTO)

    body: ft.Control = (ft.ListView([tile(p) for p in profiles], spacing=theme.SPACE, expand=True)
                        if profiles else empty_state(ft.Icons.DNS, "No servers yet",
                                                     "Tap Add to save your first server."))
    return ft.Container(padding=theme.SPACE, expand=True,
                        content=ft.Column([top, recent_bar, body], expand=True, spacing=theme.SPACE))


async def _quick_connect(ctx: AppContext, p: ConnectionProfile) -> None:
    """one-tap connect from the recent-servers bar."""
    if await ctx.connect(p):
        ctx.navigate("overview")


# ---------------------------------------------------------------- editor
def open_editor(ctx: AppContext, existing: ConnectionProfile | None) -> None:
    page = ctx.page
    p = existing or ConnectionProfile(name="", host="", username="root")
    others = [x for x in ctx.conns.list() if x.id != p.id]
    saved_pw = bool(existing and existing.password_enc)
    saved_key = bool(existing and existing.key_enc)

    name = ft.TextField(label="Name", value=p.name)
    host = ft.TextField(label="Host or IP", value=p.host, keyboard_type=ft.KeyboardType.URL)
    port = ft.TextField(label="Port", value=str(p.port), keyboard_type=ft.KeyboardType.NUMBER, width=100)
    user = ft.TextField(label="Username", value=p.username, expand=True)
    password = ft.TextField(label="Password", password=True, can_reveal_password=True,
                            hint_text="Leave blank to keep saved" if saved_pw else "Blank = ask each time")
    key = ft.TextField(label="Private key (paste PEM)", multiline=True, min_lines=3, max_lines=5,
                       hint_text="Saved — paste to replace" if saved_key else "-----BEGIN OPENSSH PRIVATE KEY-----")
    passphrase = ft.TextField(label="Key passphrase (optional)", password=True, can_reveal_password=True,
                              hint_text="Leave blank to keep saved" if (existing and existing.passphrase_enc) else None)
    jump = ft.Dropdown(label="Jump host (optional)", value=p.jump_id or "",
                       options=[ft.DropdownOption(key="", text="None")] +
                               [ft.DropdownOption(key=o.id, text=o.name) for o in others])
    tags = ft.TextField(label="Tags (comma separated)", value=", ".join(p.tags))
    color = [p.color]
    swatch_row = ft.Row(wrap=True)
    # per-server custom instructions for the AI assistant.
    ai_note = ft.TextField(
        label="AI instructions for this server (optional)",
        value=getattr(p, "ai_context", "") or "",
        multiline=True, min_lines=2, max_lines=4,
        hint_text="e.g. 'Production blog — don't restart nginx during 09:00–17:00 UTC'")
    result = ft.Text("", size=12)

    def draw_swatches():
        swatch_row.controls = [
            ft.Container(width=40, height=40, border_radius=20, bgcolor=c, data=c,
                         border=ft.Border.all(3, ft.Colors.ON_SURFACE) if c == color[0] else None,
                         on_click=pick_color) for c in COLORS]

    def pick_color(e):
        color[0] = e.control.data
        draw_swatches()
        page.update()

    pw_box = ft.Column([password], visible=p.auth == "password")
    async def import_key(_):
        try:
            picker = ft.FilePicker()
            page.services.append(picker)
            page.update()
            files = await picker.pick_files(dialog_title="Select private key file")
            if files:
                key.value = Path(files[0].path).read_text("utf-8")
                page.update()
        except Exception:  # noqa: BLE001
            toast(page, "Could not read that file. Paste the key text instead.", error=True)

    # in-app SSH key generation. Generates an ed25519 key pair
    # (the modern default) and fills the private-key field; the public key is
    # shown in a dialog so the user can install it on the server.
    def generate_key(_=None):
        from services.sshkeygen import (DEFAULT_ALG, DEFAULT_RSA_BITS, SUPPORTED_ALGS,
                                         default_comment, generate_keypair)
        alg_dd = ft.Dropdown(label="Algorithm", value=DEFAULT_ALG, expand=True,
                            options=[ft.DropdownOption(key=a, text=a) for a in SUPPORTED_ALGS])
        comment_field = ft.TextField(label="Comment (optional)",
                                       value=default_comment(p.username, p.host),
                                       dense=True, expand=True)
        result_text = ft.Text("", size=11, selectable=True)

        def do_generate(_=None):
            from services.sshkeygen import generate_keypair
            alg = alg_dd.value or DEFAULT_ALG
            comment = (comment_field.value or "").strip()
            try:
                gen = generate_keypair(algorithm=alg, comment=comment)
            except (ValueError, ImportError) as e:
                result_text.value = f"✗ {e}"
                result_text.color = ft.Colors.ERROR
                page.update()
                return
            # Fill the private-key field with the generated key.
            key.value = gen.private_key
            # Show the public key + fingerprint so the user can install it.
            result_text.value = (
                f"✓ Generated {gen.algorithm} key.\n"
                f"Fingerprint: {gen.fingerprint}\n\n"
                f"Public key (add this to ~/.ssh/authorized_keys on the server):\n"
                f"{gen.public_key}"
            )
            result_text.color = ft.Colors.GREEN
            page.update()

        dlg = ft.AlertDialog(
            scrollable=True,
            title=ft.Text("Generate SSH key pair"), modal=True,
            content=ft.Container(width=420, content=ft.Column([
                ft.Text("Generates a new key pair in OpenSSH format. The private key "
                         "will fill the connection editor's key field; the public key "
                         "below must be installed on the server before you can connect.",
                         size=12, opacity=0.7),
                ft.Row([alg_dd, comment_field], spacing=8),
                ft.FilledTonalButton("Generate", icon=ft.Icons.KEY, on_click=do_generate),
                result_text,
            ], scroll=ft.ScrollMode.AUTO, tight=True)),
            actions=[
                ft.TextButton("Install on server…", on_click=lambda _: _install_generated_key(page, ctx, result_text)),
                ft.TextButton("Done", on_click=lambda _: page.pop_dialog()),
            ])
        page.show_dialog(dlg)

    def _install_generated_key(page, ctx, result_text):
        """Parse the public key from the result_text and install it on the server
        via the existing authorized_keys flow. Only available when connected."""
        from services.sshkeygen import public_key_for_authorized_keys
        # Extract the public key line from the result text.
        lines = (result_text.value or "").splitlines()
        pub_line = ""
        for ln in lines:
            if ln.startswith("ssh-"):
                pub_line = ln.strip()
                break
        if not pub_line:
            toast(page, "Generate a key first.", error=True)
            return
        if ctx.ssh.status.value != "connected":
            toast(page, "Connect to a server first, then use this to install the key.",
                  error=True)
            return
        page.run_task(_install_key_on_server, ctx, pub_line)

    async def _install_key_on_server(ctx, pub_key_line):
        """Install the public key on the currently-connected server's user account."""
        from services.sshkeygen import install_command, is_already_installed
        from services.users import validate_new_key, parse_authorized_keys
        from core.errors import TakError
        # Read the current authorized_keys for the logged-in user.
        home = ctx.ssh.home
        auth_file = f"{home.rstrip('/')}/.ssh/authorized_keys"
        try:
            r = await ctx.ssh.run(f"cat {auth_file} 2>/dev/null || true", timeout=10)
            existing_text = r.stdout
        except TakError as e:
            toast(ctx.page, e.message, error=True)
            return
        # Check it's not already installed.
        if is_already_installed(existing_text, pub_key_line):
            toast(ctx.page, "That key is already authorized on this server.")
            return
        # Validate the key format.
        parsed, err = validate_new_key(pub_key_line, parse_authorized_keys(existing_text))
        if err:
            toast(ctx.page, err, error=True)
            return
        # Confirm before writing.
        if not await ctx.safe_confirm("Install public key",
                                       f"Append this key to {auth_file}?\n\n{pub_key_line}"):
            return
        # Install: mkdir + append.
        mkdir_cmd, append_cmd = install_command(home, pub_key_line)
        try:
            await ctx.ssh.run(mkdir_cmd, timeout=10)
            await ctx.ssh.run(append_cmd, timeout=10)
        except TakError as e:
            toast(ctx.page, e.message, error=True)
            return
        ctx.log_action(f"install authorized_keys for {home}", 0, "action")
        toast(ctx.page, f"✓ Public key installed. You can now connect with the new key.")

    key_box = ft.Column([key,
                         ft.Row([
                             ft.TextButton("Import key file", icon=ft.Icons.FILE_OPEN, on_click=import_key),
                             ft.TextButton("Generate new…", icon=ft.Icons.KEY, on_click=generate_key),
                         ], spacing=8),
                         passphrase], visible=p.auth == "key")
    auth = ft.SegmentedButton(
        selected=[p.auth],
        segments=[ft.Segment(value="password", label=ft.Text("Password")),
                  ft.Segment(value="key", label=ft.Text("SSH key"))],
        on_change=lambda e: (setattr(pw_box, "visible", "password" in e.control.selected),
                             setattr(key_box, "visible", "key" in e.control.selected), page.update()))
    draw_swatches()

    def collect() -> tuple[ConnectionProfile, Secrets]:
        try:
            prt = int(port.value)
        except ValueError:
            prt = 0
        prof = replace(p, name=name.value.strip(), host=host.value.strip(), port=prt,
                       username=user.value.strip(), auth=next(iter(auth.selected)),
                       tags=[t.strip() for t in tags.value.split(",") if t.strip()],
                       color=color[0], jump_id=jump.value or "",
                       ai_context=(ai_note.value or "").strip())
        sec = Secrets(password=password.value or None,
                      private_key=(key.value.strip() + "\n") if key.value.strip() else None,
                      passphrase=passphrase.value or None)
        return prof, sec

    async def test(_):
        try:
            prof, sec = collect()
            ctx.conns.validate(prof)
            if not await ctx.ensure_unlocked():
                return
            creds = ctx.conns.credentials(prof, ctx.vault, sec)
            if not await ctx.fill_password(creds, prof.auth):
                return
        except (ConnectionConfigError, VaultError) as e:
            result.value, result.color = f"✗ {e}", ft.Colors.ERROR
            page.update()
            return
        result.value, result.color = "Testing…", None
        page.update()
        ok, msg = await ctx.test(creds)
        result.value = ("✓ " if ok else "✗ ") + msg
        result.color = ft.Colors.GREEN if ok else ft.Colors.ERROR
        page.update()

    async def save(_):
        prof, sec = collect()
        try:
            ctx.conns.validate(prof)
            has_secret = any(v is not None for v in (sec.password, sec.private_key, sec.passphrase))
            if has_secret and not await ctx.ensure_unlocked():
                return
            ctx.conns.save(prof, sec if has_secret else None, ctx.vault)
        except (ConnectionConfigError, VaultError) as e:
            toast(page, str(e), error=True)
            return
        page.pop_dialog()
        ctx.refresh_ui()

    page.show_dialog(ft.AlertDialog(
                title=ft.Text("Edit server" if existing else "Add server"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column(
            [name, host, ft.Row([user, port]), auth, pw_box, key_box, jump, tags,
             ft.Text("Color", size=12, opacity=0.7), swatch_row,
             ft.Divider(height=1),
             ft.Text("AI instructions", size=12, opacity=0.7),
             ai_note,
             ft.Text("Added to the AI assistant's system prompt when this server is active. "
                     "Use it for hard constraints the model should never violate.",
                     size=11, opacity=0.6),
             result],
            tight=True, spacing=theme.SPACE)),
        actions=[ft.TextButton("Test", on_click=test),
                 ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                 ft.FilledButton("Save", on_click=save)]))


# ---------------------------------------------------------------- import / export
async def do_export(ctx: AppContext) -> None:
    page = ctx.page
    if not ctx.conns.list():
        toast(page, "Nothing to export.")
        return
    pw = await ctx.ask_text("Export connections",
                            "Choose an export passphrase (min 8 chars)", password=True, ok="Export")
    if pw is None:
        return
    if len(pw) < 8:
        toast(page, "Passphrase must be at least 8 characters.", error=True)
        return
    try:
        if not await ctx.ensure_unlocked():
            return
        blob = await asyncio.to_thread(ctx.conns.export_encrypted, ctx.vault, pw)
    except (VaultError, ConnectionConfigError) as e:
        toast(page, str(e), error=True)
        return
    page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text("Encrypted export"),
        content=ft.Column([
            ft.Text("Long-press the text to select and copy it. It contains your saved credentials, "
                    "encrypted with the passphrase you just chose. Keep that passphrase safe.", size=12),
            ft.TextField(value=blob, multiline=True, min_lines=4, max_lines=6, read_only=True)],
            tight=True, width=340),
        actions=[ft.FilledButton("Close", on_click=lambda _: page.pop_dialog())]))


def open_import(ctx: AppContext) -> None:
    page = ctx.page
    text = ft.TextField(label="Paste export text", multiline=True, min_lines=4, max_lines=6)
    pw = ft.TextField(label="Export passphrase", password=True, can_reveal_password=True)

    async def do(_):
        try:
            if not await ctx.ensure_unlocked():
                return
            created = await asyncio.to_thread(ctx.conns.import_encrypted, text.value, pw.value, ctx.vault)
        except (VaultError, ConnectionConfigError) as e:
            toast(page, str(e), error=True)
            return
        page.pop_dialog()
        ctx.refresh_ui()
        toast(page, f"Imported {len(created)} server(s).")

    page.show_dialog(ft.AlertDialog(
                title=ft.Text("Import connections"), scrollable=True,
        content=ft.Container(width=340, content=ft.Column([text, pw], tight=True)),
        actions=[ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                 ft.FilledButton("Import", on_click=do)]))
