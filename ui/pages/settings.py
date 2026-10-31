"""Settings page: appearance, startup, security."""
import asyncio

import flet as ft

from services.ai.chat import MODES
from storage.settings import default_download_dir
from storage.vault import VaultError
from .. import theme
from ..components.common import toast
from ..context import AppContext
from .chat import open_profiles


def settings_page(ctx: AppContext) -> ft.Control:
    page, store = ctx.page, ctx.store
    s = store.data

    def apply_and_save():
        store.save()
        theme.apply(page, s.theme_mode, s.preset, s.accent)
        page.update()

    def set_mode(e):
        s.theme_mode = list(e.control.selected)[0]
        apply_and_save()

    def set_preset(e):
        s.preset = e.control.value
        apply_and_save()

    def set_accent(e):
        s.accent = e.control.data
        s.preset = "default"
        apply_and_save()

    def set_safe(e):
        s.safe_mode = e.control.value
        store.save()

    def set_launch(e):
        s.connect_on_launch = e.control.value
        store.save()

    def set_hidden(e):
        s.files_hidden = e.control.value
        store.save()

    def set_ai_mode(e):
        s.ai_default_mode = e.control.value
        store.save()

    def set_ai_steps(e):
        try:
            s.ai_step_limit = max(1, min(30, int(e.control.value)))
            store.save()
        except ValueError:
            pass

    def set_ai_native_tools(e):
        s.ai_native_tools = bool(e.control.value)
        store.save()

    def set_ai_show_tool_calls(e):
        s.ai_show_tool_calls = bool(e.control.value)
        store.save()

    def set_term_font(e):
        s.terminal_font_size = int(e.control.value)
        store.save()

    def set_backup(e):
        s.editor_backup = e.control.value
        store.save()

    def set_font(e):
        s.editor_font_size = int(e.control.value)
        store.save()

    def set_dl_dir(e):
        s.download_dir = (e.control.value or "").strip()
        store.save()

    def set_interval(e):
        s.monitor_interval = float(e.control.value)
        store.save()

    def set_line_charts(e):
        s.monitor_line_charts = bool(e.control.value)
        store.save()

    def _set_fleet_conc(e, s, store):
        try:
            s.fleet_concurrency = max(1, min(16, int(e.control.value)))
            store.save()
        except (ValueError, TypeError):
            pass

    def set_ai_prefer_local(e):
        s.ai_prefer_local = bool(e.control.value)
        store.save()

    def thr_slider(label, attr, lo=30):
        cur = getattr(s, attr)

        def changed(e):
            setattr(s, attr, [int(e.control.start_value), int(e.control.end_value)])
            store.save()
        return ft.Column([ft.Text(f"{label}: warn / critical (%)", size=13),
                          ft.RangeSlider(min=lo, max=100, divisions=100 - lo, start_value=cur[0],
                                         end_value=cur[1], label="{value}%", on_change_end=changed)],
                         spacing=0)

    async def toggle_passphrase(e):
        sw = e.control
        want = sw.value
        try:
            if want:
                pw = await ctx.ask_text("Protect credentials", "New passphrase (min 8 characters)",
                                        password=True, ok="Next")
                again = await ctx.ask_text("Protect credentials", "Repeat passphrase",
                                           password=True, ok="Enable") if pw else None
                if not pw or len(pw) < 8 or pw != again:
                    toast(page, "Passphrase too short or did not match.", error=True)
                    sw.value = False
                else:
                    if not await ctx.ensure_unlocked():
                        sw.value = False
                    else:
                        await asyncio.to_thread(ctx.vault.enable_passphrase, pw)
                        toast(page, "Passphrase protection enabled. You'll be asked for it when needed.")
            else:
                if await ctx.ensure_unlocked():
                    ctx.vault.disable_passphrase()
                    toast(page, "Passphrase protection disabled.")
                else:
                    sw.value = True
        except VaultError as ex:
            toast(page, str(ex), error=True)
            sw.value = not want
        page.update()

    async def toggle_lock(e):
        sw = e.control
        want = sw.value
        if want:
            pin = await ctx.ask_text("Set a PIN", "4 to 8 digits", password=True, ok="Next")
            again = await ctx.ask_text("Set a PIN", "Repeat the PIN", password=True, ok="Enable") if pin else None
            err = ctx.applock.enable(pin) if pin and pin == again else "The PINs didn't match."
            if pin is None or err:
                if pin is not None:
                    toast(page, err or "Cancelled.", error=True)
                sw.value = False
            else:
                toast(page, "App lock enabled.")
        else:
            cur = await ctx.ask_text("Turn off app lock", "Current PIN", password=True, ok="Turn off")
            if cur is None or not ctx.applock.disable(cur):
                if cur is not None:
                    toast(page, "Wrong PIN, or too many attempts. Try again shortly.", error=True)
                sw.value = True
        page.update()

    def set_lock_timeout(e):
        ctx.applock.set_timeout(int(e.control.value))

    swatches = ft.Row(wrap=True, controls=[
        ft.Container(width=44, height=44, border_radius=22, bgcolor=c, data=c, on_click=set_accent)
        for c in ["#3B82F6", "#22C55E", "#EF4444", "#F59E0B", "#A855F7", "#14B8A6"]])
    h = lambda t: ft.Text(t, size=18, weight=ft.FontWeight.W_600)  # noqa: E731

    return ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
        h("Appearance"),
        ft.SegmentedButton(selected=[s.theme_mode], on_change=set_mode,
                           segments=[ft.Segment(value=v, label=ft.Text(v.title()))
                                     for v in ("light", "dark", "system")]),
        ft.Dropdown(label="Preset theme", value=s.preset, on_select=set_preset,
                    options=[ft.DropdownOption(key=k, text=k.title()) for k in theme.PRESETS]),
        ft.Text("Accent color"), swatches,
        ft.Divider(), h("Monitoring"),
        ft.Dropdown(label="Refresh interval", value=str(int(s.monitor_interval)) if s.monitor_interval == int(s.monitor_interval) else str(s.monitor_interval),
                    on_select=set_interval,
                    options=[ft.DropdownOption(key=k, text=f"{k} s") for k in ("1", "2", "5", "10", "30")]),
        thr_slider("CPU", "thr_cpu"), thr_slider("RAM", "thr_ram"), thr_slider("Disk", "thr_disk"),
        ft.Switch(label="Use line charts (Phase 14)", value=s.monitor_line_charts, on_change=set_line_charts),
        ft.Text("Real `flet-charts.LineChart` sparklines in the Monitor instead of the bar fallback. "
                "Falls back automatically when the package isn't installed.",
                size=12, opacity=0.7),
        ft.Text("Polling runs only while the Monitor page is open.", size=12, opacity=0.7),
        ft.TextField(label="Fleet concurrency (1–16)", value=str(s.fleet_concurrency), width=160,
                     keyboard_type=ft.KeyboardType.NUMBER, on_change=lambda e: _set_fleet_conc(e, s, store)),
        ft.Text("Max parallel SSH connections when probing or running commands across all servers. "
                "Lower this on devices with low file-descriptor limits.", size=12, opacity=0.7),
        ft.Divider(), h("Files"),
        ft.Switch(label="Show hidden files", value=s.files_hidden, on_change=set_hidden),
        ft.TextField(label="Download folder", value=s.download_dir, hint_text=str(default_download_dir()),
                     on_change=set_dl_dir),
        ft.Text("Leave empty for the default. On Android the default is the app's private storage; "
                "use “Save a copy…” in Transfers to put a file somewhere visible.", size=12, opacity=0.7),
        ft.Divider(), h("AI assistant"),
        ft.FilledTonalButton("AI profiles…", icon=ft.Icons.SMART_TOY, on_click=lambda _: open_profiles(ctx)),
        ft.Dropdown(label="Default mode", value=s.ai_default_mode, on_select=set_ai_mode,
                    options=[ft.DropdownOption(key=k, text=v) for k, v in MODES.items()]),
        ft.TextField(label="Agent step limit", value=str(s.ai_step_limit), width=160,
                     keyboard_type=ft.KeyboardType.NUMBER, on_change=set_ai_steps),
        ft.Switch(label="Native tool-calling (Phase 13)", value=s.ai_native_tools, on_change=set_ai_native_tools),
        ft.Switch(label="Show raw tool calls (Phase 14)", value=s.ai_show_tool_calls, on_change=set_ai_show_tool_calls),
        ft.Switch(label="Prefer local model (Phase 15)", value=s.ai_prefer_local, on_change=set_ai_prefer_local),
        ft.Text("When on, the assistant prefers a local model (Ollama / LM Studio / vLLM on your "
                "LAN) over a cloud provider when both are configured. Falls back to cloud when no "
                "local profile exists.", size=12, opacity=0.7),
        ft.Text("When on, assistant messages that used native tool-calling show a collapsible "
                "block with the exact tool-call payloads (function name, args, and the result), "
                "so you can inspect what the model sent. Off by default — only enable for debugging.",
                size=12, opacity=0.7),
        ft.Text("When on (default), the assistant uses the provider's native function-calling "
                "protocol (OpenAI / Anthropic / Gemini) instead of parsing bash blocks from the "
                "model's text. Falls back automatically for providers or models that don't support it.",
                size=12, opacity=0.7),
        ft.Text("Bring your own key. Messages go straight from this device to your provider; secrets are "
                "redacted first. Commands never run without the approval rules of the chosen mode.",
                size=12, opacity=0.7),
        ft.Divider(), h("Terminal"),
        ft.Dropdown(label="Font size", value=str(s.terminal_font_size), on_select=set_term_font,
                    options=[ft.DropdownOption(key=str(n), text=f"{n} pt") for n in (9, 10, 11, 12, 14, 16)]),
        ft.Switch(label="Auto-scroll to bottom on new output", value=s.terminal_auto_scroll,
                  on_change=lambda e: (setattr(s, "terminal_auto_scroll", bool(e.control.value)), store.save())),
        ft.Text("When on (default), the terminal always jumps to the latest output. "
                "Turn off if you want to scroll up and read older output without being "
                "pulled back down.", size=12, opacity=0.7),
        ft.Divider(), h("Editor"),
        ft.Dropdown(label="Font size", value=str(s.editor_font_size), on_select=set_font,
                    options=[ft.DropdownOption(key=str(n), text=f"{n} pt") for n in (11, 12, 14, 16, 18, 20)]),
        ft.Switch(label="Back up the original file before overwriting (.takvps.bak)",
                  value=s.editor_backup, on_change=set_backup),
        ft.Divider(), h("Startup"),
        ft.Switch(label="Connect to last used server on launch", value=s.connect_on_launch,
                  on_change=set_launch),
        ft.Divider(), h("Security"),
        ft.Switch(label="Protect credentials with a passphrase",
                  value=ctx.vault.mode == "passphrase", on_change=toggle_passphrase),
        ft.Text("Without a passphrase, credentials are encrypted with a key stored in the app's "
                "private storage. A passphrase is stronger but must be entered after each launch.",
                size=12, opacity=0.7),
        ft.Switch(label="App lock (PIN)", value=ctx.applock.enabled, on_change=toggle_lock),
        ft.Dropdown(label="Lock after leaving the app for", value=str(ctx.applock.timeout), on_select=set_lock_timeout,
                    options=[ft.DropdownOption(key=str(k), text=t) for k, t in ((0, "Immediately"), (60, "1 minute"), (300, "5 minutes"), (900, "15 minutes"))]),
        ft.Text("The PIN hides the app and slows guessing down (delays after 5 wrong tries). It does not encrypt your credentials "
                "— for that, use the passphrase option above. Biometric unlock isn't available in this build.", size=12, opacity=0.7),
        ft.Switch(label="Safe mode (ask before EVERY change: file edits, renames, cron, keys, signals, deploys, …)",
                  value=s.safe_mode, on_change=set_safe),
        ft.Divider(), h("Backup & restore (Phase 14)"),
        ft.Text("Export the entire app state — connections, snippets, AI profiles, and settings — "
                "as one encrypted bundle. Paste the text on a new phone and enter the same passphrase "
                "to restore everything at once.", size=12, opacity=0.7),
        ft.Row([
            ft.FilledTonalButton("Export bundle…", icon=ft.Icons.ARCHIVE, on_click=lambda _: _export_bundle(ctx)),
            ft.FilledTonalButton("Import bundle…", icon=ft.Icons.UNARCHIVE, on_click=lambda _: _import_bundle(ctx)),
        ], spacing=8, scroll=ft.ScrollMode.AUTO),
        ft.Text("The bundle includes decrypted secrets (passwords, private keys, API keys), but "
                "they're re-encrypted with your passphrase before leaving the device. Use a strong "
                "passphrase you won't forget — there's no recovery.", size=11, opacity=0.6),
    ])


# ---- Phase 14 Part 10: encrypted export bundles ----

def _export_bundle(ctx: AppContext) -> None:
    """Collect all app state, encrypt with a user passphrase, show the blob."""
    page = ctx.page

    async def do_export(passphrase: str) -> None:
        from services.app_bundle import collect_bundle, encrypt_bundle
        try:
            if not await ctx.ensure_unlocked():
                return
            bundle = collect_bundle(
                connections_store=ctx.conns, snippets_store=ctx.snippets,
                ai_profiles_store=ctx.ai_profiles, settings_store=ctx.store,
                vault=ctx.vault)
            blob = encrypt_bundle(bundle, passphrase)
        except Exception as e:  # noqa: BLE001
            toast(page, f"Export failed: {e}", error=True)
            return
        page.pop_dialog()
        # Show the blob in a selectable text field + a copy button.
        field = ft.TextField(value=blob, multiline=True, min_lines=6, max_lines=12,
                              border_radius=8, text_size=10, font_family="monospace")

        async def copy_blob(_) -> None:
            try:
                clip = ft.Clipboard()
                page.services.append(clip)
                page.update()
                await clip.set(blob)
                toast(page, "Bundle copied to clipboard.")
            except Exception:  # noqa: BLE001
                toast(page, "Couldn't access the clipboard.", error=True)

        page.show_dialog(ft.AlertDialog(
            scrollable=True,
            title=ft.Text("Export bundle"), modal=True,
            content=ft.Container(width=420, content=ft.Column([
                ft.Text(f"Encrypted bundle ({len(blob):,} chars). Copy this text and save it "
                         "somewhere safe — it contains all your decrypted secrets, encrypted "
                         "with your passphrase.", size=12, opacity=0.7),
                field,
            ], tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[
                ft.TextButton("Copy", on_click=lambda _: page.run_task(copy_blob)),
                ft.TextButton("Close", on_click=lambda _: page.pop_dialog()),
            ]))

    _ask_passphrase(ctx, "Export bundle", do_export)


def _import_bundle(ctx: AppContext) -> None:
    """Paste a bundle blob, enter the passphrase, restore everything."""
    page = ctx.page
    blob_field = ft.TextField(hint_text="Paste the bundle text here…", multiline=True,
                               min_lines=4, max_lines=8, border_radius=8, text_size=10,
                               font_family="monospace")

    async def do_import(passphrase: str) -> None:
        from services.app_bundle import decrypt_bundle, bundle_meta, apply_bundle
        blob = (blob_field.value or "").strip()
        if not blob:
            toast(page, "Paste the bundle text first.", error=True)
            return
        try:
            if not await ctx.ensure_unlocked():
                return
            bundle = decrypt_bundle(blob, passphrase)
        except ValueError as e:
            toast(page, str(e), error=True)
            return
        meta = bundle_meta(bundle)
        # Show a preview before applying.
        if not await ctx.safe_confirm("Import bundle",
                                       f"{meta.summary()}\n\nThis will ADD to your existing data "
                                       "(nothing is deleted). Continue?"):
            return
        try:
            counts = apply_bundle(
                bundle,
                connections_store=ctx.conns, snippets_store=ctx.snippets,
                ai_profiles_store=ctx.ai_profiles, settings_store=ctx.store,
                vault=ctx.vault)
        except Exception as e:  # noqa: BLE001
            toast(page, f"Import failed: {e}", error=True)
            return
        page.pop_dialog()
        page.pop_dialog()     # close the passphrase dialog too
        parts = [f"{v} {k}" for k, v in counts.items() if v]
        toast(page, f"Imported: {' · '.join(parts)}")
        ctx.refresh_ui()

    _ask_passphrase(ctx, "Import bundle", do_import, extra_content=blob_field)


def _ask_passphrase(ctx: AppContext, title: str, on_ok, extra_content: ft.Control = None) -> None:
    """Show a dialog asking for a passphrase, then call on_ok(passphrase)."""
    page = ctx.page
    pw_field = ft.TextField(label="Passphrase", password=True, can_reveal_password=True,
                             autofocus=True, border_radius=8, expand=True)
    col_controls: list[ft.Control] = [pw_field]
    if extra_content:
        col_controls.append(extra_content)
    col_controls.append(ft.Text("Choose a strong passphrase. There is no recovery if you forget it.",
                                 size=11, opacity=0.6))

    async def confirm(_=None) -> None:
        pw = pw_field.value or ""
        if len(pw) < 4:
            toast(page, "Passphrase must be at least 4 characters.", error=True)
            return
        page.pop_dialog()
        await on_ok(pw)

    pw_field.on_submit = confirm
    page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text(title), modal=True,
        content=ft.Container(width=380, content=ft.Column(col_controls, tight=True, spacing=8)),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.FilledButton("Continue", on_click=lambda _: page.run_task(confirm)),
        ]))
