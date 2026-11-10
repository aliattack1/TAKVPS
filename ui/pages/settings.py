"""Settings page: appearance, startup, security."""
import asyncio

import flet as ft

from services.ai.chat import MODES
from storage.settings import default_download_dir
from storage.vault import VaultError
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext
from .chat import open_profiles


def settings_page(ctx: AppContext) -> ft.Control:
    page, store = ctx.page, ctx.store
    s = store.data

    def apply_and_save():
        store.save()
        theme.apply(page, s.theme_mode, s.preset, s.accent, s.language)
        # Force a full re-render so the navigation rail, drawer, and FAB get
        # rebuilt with the new language/RTL direction. Without this, switching
        # to Persian leaves the previously-rendered controls in LTR.
        try:
            from ..shell import Shell  # local import to avoid circular
            shell = getattr(page, "_takvps_shell", None)
            if shell is not None:
                shell.render()
        except Exception:  # noqa: BLE001
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
                    toast(page, _t("Passphrase too short or did not match."), error=True)
                    sw.value = False
                else:
                    if not await ctx.ensure_unlocked():
                        sw.value = False
                    else:
                        await asyncio.to_thread(ctx.vault.enable_passphrase, pw)
                        toast(page, _t("Passphrase protection enabled. You'll be asked for it when needed."))
            else:
                if await ctx.ensure_unlocked():
                    ctx.vault.disable_passphrase()
                    toast(page, _t("Passphrase protection disabled."))
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
                toast(page, _t("App lock enabled."))
        else:
            cur = await ctx.ask_text("Turn off app lock", "Current PIN", password=True, ok="Turn off")
            if cur is None or not ctx.applock.disable(cur):
                if cur is not None:
                    toast(page, _t("Wrong PIN, or too many attempts. Try again shortly."), error=True)
                sw.value = True
        page.update()

    def set_lock_timeout(e):
        ctx.applock.set_timeout(int(e.control.value))

    swatches = ft.Row(wrap=True, controls=[
        ft.Container(width=44, height=44, border_radius=22, bgcolor=c, data=c, on_click=set_accent)
        for c in ["#3B82F6", "#22C55E", "#EF4444", "#F59E0B", "#A855F7", "#14B8A6"]])

    # Persian translation helper for section headers and switch labels.
    from services.i18n import translate
    lang = s.language
    _t = lambda t: translate(t, lang)  # noqa: E731
    h = lambda t: ft.Text(_t(t), size=18, weight=ft.FontWeight.W_600)  # noqa: E731

    return ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, controls=[
        h("Appearance"),
        ft.Dropdown(label=_t("Language / زبان"), value=s.language,
                    on_select=lambda e: (setattr(s, "language", e.control.value), apply_and_save()),
                    options=[ft.DropdownOption(key="en", text="English"),
                             ft.DropdownOption(key="fa", text="فارسی (Persian)")]),
        ft.SegmentedButton(selected=[s.theme_mode], on_change=set_mode,
                           segments=[ft.Segment(value=v, label=ft.Text(_t(v.title())))
                                     for v in ("light", "dark", "system")]),
        ft.Dropdown(label=_t("Preset theme"), value=s.preset, on_select=set_preset,
                    options=[ft.DropdownOption(key=k, text=k.title()) for k in theme.PRESETS]),
        ft.Text(_t("Accent color")), swatches,
        ft.Divider(), h("Monitoring"),
        ft.Dropdown(label=_t("Refresh interval"), value=str(int(s.monitor_interval)) if s.monitor_interval == int(s.monitor_interval) else str(s.monitor_interval),
                    on_select=set_interval,
                    options=[ft.DropdownOption(key=k, text=f"{k} s") for k in ("1", "2", "5", "10", "30")]),
        thr_slider(_t("CPU"), "thr_cpu"), thr_slider(_t("RAM"), "thr_ram"), thr_slider(_t("Disk"), "thr_disk"),
        ft.Switch(label=_t("Use line charts"), value=s.monitor_line_charts, on_change=set_line_charts),
        ft.Text(_t("Real `flet-charts.LineChart` sparklines in the Monitor instead of the bar fallback. "
                "Falls back automatically when the package isn't installed."),
                size=12, opacity=0.7),
        ft.Text(_t("Polling runs only while the Monitor page is open."), size=12, opacity=0.7),
        ft.TextField(label=_t("Fleet concurrency (1–16)"), value=str(s.fleet_concurrency), width=160,
                     keyboard_type=ft.KeyboardType.NUMBER, on_change=lambda e: _set_fleet_conc(e, s, store)),
        ft.Text(_t("Max parallel SSH connections when probing or running commands across all servers. "
                "Lower this on devices with low file-descriptor limits."), size=12, opacity=0.7),
        ft.Divider(), h("Files"),
        ft.Switch(label=_t("Show hidden files"), value=s.files_hidden, on_change=set_hidden),
        ft.TextField(label=_t("Download folder"), value=s.download_dir, hint_text=str(default_download_dir()),
                     on_change=set_dl_dir),
        ft.Text(_t("Leave empty for the default. On Android the default is the app's private storage; "
                "use “Save a copy…” in Transfers to put a file somewhere visible."), size=12, opacity=0.7),
        ft.Divider(), h("AI assistant"),
        ft.FilledTonalButton(_t("AI profiles…"), icon=ft.Icons.SMART_TOY, on_click=lambda _: open_profiles(ctx)),
        ft.Dropdown(label=_t("Default mode"), value=s.ai_default_mode, on_select=set_ai_mode,
                    options=[ft.DropdownOption(key=k, text=v) for k, v in MODES.items()]),
        ft.TextField(label=_t("Agent step limit"), value=str(s.ai_step_limit), width=160,
                     keyboard_type=ft.KeyboardType.NUMBER, on_change=set_ai_steps),
        ft.Switch(label=_t("Native tool-calling"), value=s.ai_native_tools, on_change=set_ai_native_tools),
        ft.Switch(label=_t("Show raw tool calls"), value=s.ai_show_tool_calls, on_change=set_ai_show_tool_calls),
        ft.Switch(label=_t("Prefer local model"), value=s.ai_prefer_local, on_change=set_ai_prefer_local),
        ft.Text(_t("When on, the assistant prefers a local model (Ollama / LM Studio / vLLM on your "
                "LAN) over a cloud provider when both are configured. Falls back to cloud when no "
                "local profile exists."), size=12, opacity=0.7),
        ft.Text(_t("When on, assistant messages that used native tool-calling show a collapsible "
                "block with the exact tool-call payloads (function name, args, and the result), "
                "so you can inspect what the model sent. Off by default — only enable for debugging."),
                size=12, opacity=0.7),
        ft.Text(_t("When on (default), the assistant uses the provider's native function-calling "
                "protocol (OpenAI / Anthropic / Gemini) instead of parsing bash blocks from the "
                "model's text. Falls back automatically for providers or models that don't support it."),
                size=12, opacity=0.7),
        ft.Text(_t("Bring your own key. Messages go straight from this device to your provider; secrets are "
                "redacted first. Commands never run without the approval rules of the chosen mode."),
                size=12, opacity=0.7),
        ft.Divider(), h("Terminal"),
        ft.Dropdown(label=_t("Font size"), value=str(s.terminal_font_size), on_select=set_term_font,
                    options=[ft.DropdownOption(key=str(n), text=f"{n} pt") for n in (9, 10, 11, 12, 14, 16)]),
        ft.Switch(label=_t("Auto-scroll to bottom on new output"), value=s.terminal_auto_scroll,
                  on_change=lambda e: (setattr(s, "terminal_auto_scroll", bool(e.control.value)), store.save())),
        ft.Text(_t("When on (default), the terminal always jumps to the latest output. "
                "Turn off if you want to scroll up and read older output without being "
                "pulled back down."), size=12, opacity=0.7),
        ft.Divider(), h("Editor"),
        ft.Dropdown(label=_t("Font size"), value=str(s.editor_font_size), on_select=set_font,
                    options=[ft.DropdownOption(key=str(n), text=f"{n} pt") for n in (11, 12, 14, 16, 18, 20)]),
        ft.Switch(label=_t("Back up the original file before overwriting (.takvps.bak)"),
                  value=s.editor_backup, on_change=set_backup),
        ft.Divider(), h("Startup"),
        ft.Switch(label=_t("Connect to last used server on launch"), value=s.connect_on_launch,
                  on_change=set_launch),
        ft.Divider(), h("Security"),
        ft.Switch(label=_t("Protect credentials with a passphrase"),
                  value=ctx.vault.mode == "passphrase", on_change=toggle_passphrase),
        ft.Text(_t("Without a passphrase, credentials are encrypted with a key stored in the app's "
                "private storage. A passphrase is stronger but must be entered after each launch."),
                size=12, opacity=0.7),
        ft.Switch(label=_t("App lock (PIN)"), value=ctx.applock.enabled, on_change=toggle_lock),
        ft.Dropdown(label=_t("Lock after leaving the app for"), value=str(ctx.applock.timeout), on_select=set_lock_timeout,
                    options=[ft.DropdownOption(key=str(k), text=_t(t)) for k, t in ((0, "Immediately"), (60, "1 minute"), (300, "5 minutes"), (900, "15 minutes"))]),
        ft.Text(_t("The PIN hides the app and slows guessing down (delays after 5 wrong tries). It does not encrypt your credentials "
                "— for that, use the passphrase option above. Biometric unlock isn't available in this build."), size=12, opacity=0.7),
        ft.Switch(label=_t("Safe mode (ask before EVERY change: file edits, renames, cron, keys, signals, deploys, …)"),
                  value=s.safe_mode, on_change=set_safe),
        ft.Divider(), h("Backup & restore"),
        ft.Text(_t("Export the entire app state — connections, snippets, AI profiles, and settings — "
                "as one encrypted bundle. Paste the text on a new phone and enter the same passphrase "
                "to restore everything at once."), size=12, opacity=0.7),
        ft.Row([
            ft.FilledTonalButton(_t("Export bundle…"), icon=ft.Icons.ARCHIVE, on_click=lambda _: _export_bundle(ctx)),
            ft.FilledTonalButton(_t("Import bundle…"), icon=ft.Icons.UNARCHIVE, on_click=lambda _: _import_bundle(ctx)),
        ], spacing=8, scroll=ft.ScrollMode.AUTO),
        ft.Text(_t("The bundle includes decrypted secrets (passwords, private keys, API keys), but "
                "they're re-encrypted with your passphrase before leaving the device. Use a strong "
                "passphrase you won't forget — there's no recovery."), size=11, opacity=0.6),
        ft.Divider(), h("About"),
        ft.Row([
            ft.FilledTonalButton(_t("Check for updates"), icon=ft.Icons.SYSTEM_UPDATE,
                                 on_click=lambda _: _myket_check_update(ctx)),
            ft.FilledTonalButton(_t("Rate on Myket"), icon=ft.Icons.STAR,
                                 on_click=lambda _: _myket_rate(ctx)),
            ft.FilledTonalButton(_t("App page"), icon=ft.Icons.INFO,
                                 on_click=lambda _: _myket_open(ctx)),
        ], spacing=8, scroll=ft.ScrollMode.AUTO),
        ft.Text(f"TAKVPS v1.4.5 · {_t('Client-only SSH/VPS manager')}",
                size=11, opacity=0.5),
    ])


# ---- encrypted export bundles ----

def _export_bundle(ctx: AppContext) -> None:
    """Collect all app state, encrypt with a user passphrase, show the blob."""
    page = ctx.page
    lang = ctx.store.data.language
    from services.i18n import translate
    _t = lambda s: translate(s, lang)  # noqa: E731

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
            toast(page, _t("Export failed:") + f" {e}", error=True)
            return
        # The passphrase dialog was already popped by _ask_passphrase's confirm().
        # Show the result in a NEW dialog.
        field = ft.TextField(value=blob, multiline=True, min_lines=6, max_lines=12,
                              border_radius=8,
                              text_style=ft.TextStyle(font_family="monospace", size=10),
                              read_only=True)

        async def copy_blob(_) -> None:
            try:
                # Reuse a cached clipboard if possible
                if not hasattr(ctx, '_clip'):
                    ctx._clip = ft.Clipboard()
                    page.services.append(ctx._clip)
                    page.update()
                await ctx._clip.set(blob)
                toast(page, _t("Bundle copied to clipboard."))
            except Exception:  # noqa: BLE001
                toast(page, _t("Couldn't access the clipboard."), error=True)

        page.show_dialog(ft.AlertDialog(
            scrollable=True,
            title=ft.Text(_t("Export bundle")), modal=True,
            content=ft.Container(width=min(420, (page.width or 400) - 32), content=ft.Column([
                ft.Text(_t("Encrypted bundle ({n} chars). Copy this text and save it "
                         "somewhere safe — it contains all your decrypted secrets, encrypted "
                         "with your passphrase.").format(n=f"{len(blob):,}"),
                         size=12, opacity=0.7),
                field,
            ], tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[
                # Fix: pass the click event `_` through to copy_blob so the
                # async coroutine receives its expected positional argument.
                # Without this, `page.run_task(copy_blob)` calls it with 0
                # args but copy_blob is defined as `async def copy_blob(_)`,
                # which raised TypeError: missing 1 required positional
                # argument.
                ft.TextButton(_t("Copy"), on_click=lambda e: page.run_task(copy_blob, e)),
                ft.TextButton(_t("Close"), on_click=lambda _: page.pop_dialog()),
            ]))

    _ask_passphrase(ctx, _t("Export bundle"), do_export)


def _import_bundle(ctx: AppContext) -> None:
    """Paste a bundle blob, enter the passphrase, restore everything."""
    page = ctx.page
    lang = ctx.store.data.language
    from services.i18n import translate
    _t = lambda s: translate(s, lang)  # noqa: E731

    blob_field = ft.TextField(hint_text=_t("Paste the bundle text here…"), multiline=True,
                               min_lines=4, max_lines=8, border_radius=8,
                               text_style=ft.TextStyle(font_family="monospace", size=10))

    async def do_import(passphrase: str) -> None:
        from services.app_bundle import decrypt_bundle, bundle_meta, apply_bundle
        blob = (blob_field.value or "").strip()
        if not blob:
            toast(page, _t("Paste the bundle text first."), error=True)
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
        if not await ctx.safe_confirm(_t("Import bundle"),
                                       f"{meta.summary()}\n\n" +
                                       _t("This will ADD to your existing data "
                                          "(nothing is deleted). Continue?")):
            return
        try:
            counts = apply_bundle(
                bundle,
                connections_store=ctx.conns, snippets_store=ctx.snippets,
                ai_profiles_store=ctx.ai_profiles, settings_store=ctx.store,
                vault=ctx.vault)
        except Exception as e:  # noqa: BLE001
            toast(page, _t("Import failed:") + f" {e}", error=True)
            return
        # Only pop if there's a dialog to pop (safe_confirm may have already been popped)
        try:
            page.pop_dialog()
        except Exception:
            pass
        parts = [f"{v} {k}" for k, v in counts.items() if v]
        toast(page, _t("Imported:") + f" {' · '.join(parts)}")
        ctx.refresh_ui()

    _ask_passphrase(ctx, _t("Import bundle"), do_import, extra_content=blob_field)


def _ask_passphrase(ctx: AppContext, title: str, on_ok, extra_content: ft.Control = None) -> None:
    """Show a dialog asking for a passphrase, then call on_ok(passphrase)."""
    page = ctx.page
    lang = ctx.store.data.language
    from services.i18n import translate
    _t = lambda s: translate(s, lang)  # noqa: E731

    pw_field = ft.TextField(label=_t("Passphrase"), password=True, can_reveal_password=True,
                             autofocus=True, border_radius=8, expand=True)
    col_controls: list[ft.Control] = [pw_field]
    if extra_content:
        col_controls.append(extra_content)
    col_controls.append(ft.Text(_t("Choose a strong passphrase. There is no recovery if you forget it."),
                                 size=11, opacity=0.6))

    async def confirm(_=None) -> None:
        pw = pw_field.value or ""
        if len(pw) < 4:
            toast(page, _t("Passphrase must be at least 4 characters."), error=True)
            return
        page.pop_dialog()
        try:
            await on_ok(pw)
        except Exception as e:  # noqa: BLE001
            toast(page, _t("Error:") + f" {e}", error=True)

    pw_field.on_submit = confirm
    page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text(title, weight=ft.FontWeight.W_600), modal=True,
        content=ft.Container(width=min(380, (page.width or 400) - 32), content=ft.Column(col_controls, tight=True, spacing=8)),
        actions=[
            ft.TextButton(_t("Cancel"), on_click=lambda _: page.pop_dialog()),
            ft.FilledButton(_t("Continue"), on_click=lambda _: page.run_task(confirm)),
        ]))


# ---- Myket store integration ----

def _myket_check_update(ctx: AppContext) -> None:
    """Open Myket's update check dialog."""
    from services.myket import check_for_update
    from services.i18n import translate
    _t = lambda s: translate(s, ctx.store.data.language)  # noqa: E731
    if not check_for_update(ctx.page):
        toast(ctx.page, _t("Couldn't open Myket. Is it installed?"), error=True)


def _myket_rate(ctx: AppContext) -> None:
    """Open the Myket rating/review flow. Asks permission first."""
    from services.i18n import translate
    _t = lambda s: translate(s, ctx.store.data.language)  # noqa: E731
    def do_rate():
        from services.myket import open_rating
        if not open_rating(ctx.page):
            toast(ctx.page, _t("Couldn't open Myket. Is it installed?"), error=True)
    confirm(ctx.page, _t("Rate on Myket?"),
            _t("This will open Myket's rating page for TAKVPS. You can rate and review the app there."),
            do_rate, yes_label=_t("Open Myket"))


def _myket_open(ctx: AppContext) -> None:
    """Open the app's Myket details page."""
    from services.myket import open_app_page
    from services.i18n import translate
    _t = lambda s: translate(s, ctx.store.data.language)  # noqa: E731
    if not open_app_page(ctx.page):
        toast(ctx.page, _t("Couldn't open Myket. Is it installed?"), error=True)
