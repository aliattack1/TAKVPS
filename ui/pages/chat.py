"""AI chat panel: controller (streaming, approval modes, command execution) + view + profile manager."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Callable, Optional
from urllib.parse import urlparse

import flet as ft

from core.errors import TakError
from services.ai import client
from services.ai.chat import (MODES, ContextOptions, Msg, build_context, build_system, estimate_tokens,
                              first_command, is_local_url, split_blocks, to_api_messages)
from services.ai.providers import DEFAULT_URLS, KINDS, ProviderError
from services.ai.tools import (ToolCall, extract_command, supports_tools, tool_result_message,
                               validate_command_safety)
from services.i18n import translate
from services.redact import redact
from services.risk import classify
from storage.ai_profiles import AIProfile
from storage.chats import export_text
from storage.vault import VaultError
from .. import theme
from ..components.common import confirm, toast
from ..context import AppContext

RISK_COLORS = {"safe": ft.Colors.GREEN, "writes": ft.Colors.AMBER_700, "destructive": ft.Colors.ORANGE_800,
               "dangerous": ft.Colors.RED}
QUICK = [("Explain this output", "Explain this terminal output in plain language."),
         ("Why did this fail?", "Why did this fail? Give the most likely cause and how to confirm it."),
         ("Fix this error", "Suggest a fix for this error."),
         ("Summarize this log", "Summarize this log: key events, errors and anything unusual."),
         ("What does this command do?", None)]


# ====================================================================== controller
class ChatController:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page, self.s = ctx, ctx.page, ctx.store.data
        self.msgs: list[Msg] = []
        self.mode = self.s.ai_default_mode
        self.conn_id: Optional[str] = None
        self.view: Optional["ChatView"] = None
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.pending_prefill: Optional[str] = None
        # Persian translation helper.
        self._lang = ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        # tool results waiting to be sent in the next request.
        # Each entry is a provider-shaped dict ready for the API call.
        self._tool_result_msgs: list[dict] = []

    # ---- state ----
    def ensure_loaded(self) -> None:
        key = self.ctx.active.id if self.ctx.active else "none"
        if key != self.conn_id:
            self.conn_id = key
            self.msgs, mode = self.ctx.chats.load(key)
            self.mode = mode if mode in MODES else self.s.ai_default_mode

    def persist(self) -> None:
        if self.conn_id:
            self.ctx.chats.save(self.conn_id, self.msgs, self.mode)

    def profile(self) -> Optional[AIProfile]:
        p = self.ctx.ai_profiles.get(self.s.ai_active_profile)
        if p is None:
            lst = self.ctx.ai_profiles.list()
            p = lst[0] if lst else None
        # if "prefer local" is on, and the active profile isn't
        # local, try to find a local profile on the same network.
        if p and getattr(self.s, "ai_prefer_local", False):
            from services.ai_routing import is_local_profile, pick_best_profile
            if not is_local_profile(p):
                lst = self.ctx.ai_profiles.list()
                best = pick_best_profile(lst, prefer_local=True)
                if best and is_local_profile(best):
                    p = best
        return p

    def build_view(self, compact: bool = False, on_close: Optional[Callable[[], None]] = None) -> ft.Control:
        self.ensure_loaded()
        self.view = ChatView(self, compact, on_close)
        return self.view.control

    def prefill(self, text: str) -> None:
        if self.view:
            self.view.set_input(text)
        else:
            self.pending_prefill = text

    # ---- context ----
    def options(self) -> ContextOptions:
        c = self.s.ai_context
        return ContextOptions(os=c.get("os", True), shell=c.get("shell", True), cwd=c.get("cwd", True),
                              pkg=c.get("pkg", True), term_lines=int(c.get("term", 0)),
                              services=c.get("services", False))

    async def context_text(self) -> str:
        opts, info = self.options(), self.ctx.sysinfo
        facts = {"cwd": self.ctx.files_state.path}
        if info:
            facts.update(distro=info.distro, kernel=info.kernel, arch=info.arch, pkg=info.package_manager,
                         sudo={"root": "root", "passwordless": "passwordless sudo", "password": "sudo needs a password",
                               "none": "no sudo"}[info.sudo])
        services = None
        if opts.services and self.ctx.ssh.status.value == "connected":
            try:
                r = await self.ctx.ssh.run("systemctl list-units --type=service --state=running --no-legend "
                                           "--plain 2>/dev/null | awk '{print $1}' | head -n 40", timeout=10)
                services = [x.strip().removesuffix(".service") for x in r.stdout.splitlines() if x.strip()]
            except TakError:
                pass
        tail = self.ctx.terminal.tail(opts.term_lines) if opts.term_lines and self.ctx.terminal else ""
        return build_context(opts, facts, tail, services)

    def _server_note(self) -> str:
        """per-server custom instructions from the active
        connection profile. Empty when no server is connected or the user
        hasn't written a note for this server."""
        a = self.ctx.active
        return getattr(a, "ai_context", "") if a else ""

    async def preview(self) -> tuple[str, int]:
        """What would be sent (redacted) and how many secrets were masked."""
        ctxt, n1 = redact(await self.context_text())
        system = build_system(self.mode, ctxt, self._server_note())
        api = to_api_messages(self.msgs, lambda t: t)
        total = n1
        shown = []
        for m in api[-6:]:
            clean, n = redact(m["content"])
            total += n
            shown.append(f"[{m['role']}]\n{clean}")
        return f"[system]\n{system}\n\n" + "\n\n".join(shown), total

    # ---- conversation ----
    async def send(self, text: str) -> None:
        text = text.strip()
        if not text or self.running:
            return
        self.ensure_loaded()
        self._add(Msg("user", text))
        self.running = True
        if self.view:
            self.view.set_running(True)
        self.task = asyncio.ensure_future(self._converse())

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()

    def _add(self, m: Msg) -> None:
        self.msgs.append(m)
        self.persist()
        if self.view:
            self.view.add(m)

    async def _converse(self) -> None:
        steps = 0
        try:
            while True:
                # prefer native tool-calling when enabled and supported;
                # otherwise fall back to the structured (bash-block) path.
                use_tools = self.s.ai_native_tools and self._provider_supports_tools()
                if use_tools:
                    reply, calls = await self._reply_with_tools()
                else:
                    reply, calls = await self._reply(), []
                if reply is None:
                    break
                if self.mode == "suggest":
                    break
                # Native tool-calling path: if the model emitted tool calls, run them.
                if calls:
                    # the assistant message `m` already has the
                    # tool_calls attached (from _reply_with_tools). We'll update
                    # each entry with its result after running, so the UI's
                    # "Show raw tool calls" toggle can display the full request→result.
                    assistant_msg = self.msgs[-1] if self.msgs and self.msgs[-1].tool_calls else None
                    for idx, call in enumerate(calls):
                        steps += 1
                        if steps > self.s.ai_step_limit:
                            self._note(f"Stopped: reached the {self.s.ai_step_limit}-step limit.")
                            break
                        ok, msg = validate_command_safety(call.args.get("command", ""))
                        if not ok:
                            self._add(Msg("result", msg, cmd="(invalid tool call)", exit=-1, ai=True))
                            if assistant_msg and idx < len(assistant_msg.tool_calls):
                                assistant_msg.tool_calls[idx]["result_text"] = msg
                                assistant_msg.tool_calls[idx]["result_exit"] = -1
                            continue
                        cmd, sudo = extract_command(call)
                        if not cmd:
                            continue
                        self._note(f"Step {steps}/{self.s.ai_step_limit}: {(cmd.splitlines()[0][:80] if cmd.splitlines() else cmd[:80])}")
                        if not await self.gate(cmd, auto_safe=self.mode == "agent"):
                            declined = "The user declined to run this command."
                            self._add(Msg("result", declined, cmd=cmd, exit=None, ai=True))
                            if assistant_msg and idx < len(assistant_msg.tool_calls):
                                assistant_msg.tool_calls[idx]["result_text"] = declined
                                assistant_msg.tool_calls[idx]["result_exit"] = None
                            break
                        result = await self.execute(cmd, ai=True)
                        self._add(result)
                        # attach the result to the tool_call entry
                        # so the UI can show the full request→result cycle.
                        if assistant_msg and idx < len(assistant_msg.tool_calls):
                            assistant_msg.tool_calls[idx]["result_text"] = result.text
                            assistant_msg.tool_calls[idx]["result_exit"] = result.exit
                        if self.view:
                            self.view.refresh_msg(assistant_msg)
                        # Feed the tool result back into the message stream so the
                        # model can continue the conversation.
                        tool_msg = tool_result_message(self.profile().kind, call,
                                                       result.text, result.exit or 0)
                        # Stash it as a synthetic assistant message; the next _reply
                        # will pick it up via to_api_messages.
                        self._tool_result_msgs.append(tool_msg)
                    continue     # loop back for the model's next reply
                # Structured fallback: look for a bash block in the text.
                cmd = first_command(reply)
                if not cmd:
                    break
                steps += 1
                if steps > self.s.ai_step_limit:
                    self._note(f"Stopped: reached the {self.s.ai_step_limit}-step limit.")
                    break
                self._note(f"Step {steps}/{self.s.ai_step_limit}: {(cmd.splitlines()[0][:80] if cmd.splitlines() else cmd[:80])}")
                if not await self.gate(cmd, auto_safe=self.mode == "agent"):
                    self._add(Msg("result", "The user declined to run this command.", cmd=cmd, exit=None, ai=True))
                    break
                self._add(await self.execute(cmd, ai=True))
        except asyncio.CancelledError:
            self._note("Stopped.")
        finally:
            self.running = False
            self.persist()
            if self.view:
                self.view.set_running(False)

    def _provider_supports_tools(self) -> bool:
        """True if the active profile's provider kind supports native tool-calling."""
        p = self.profile()
        if p is None:
            return False
        # The profile's `kind` lives on the config returned by ai_profiles.config().
        # We avoid the network round-trip (and the vault unlock) by reading the
        # stored kind directly off the profile.
        kind = getattr(p, "kind", "") or ""
        return supports_tools(kind)

    def _note(self, text: str) -> None:
        if self.view:
            self.view.note(text)

    async def _reply(self) -> Optional[str]:
        p = self.profile()
        if p is None:
            toast(self.page, self._t("Set up an AI profile first."), error=True)
            open_profiles(self.ctx)
            return None
        try:
            if p.api_key_enc and not await self.ctx.ensure_unlocked():
                return None
            cfg = self.ctx.ai_profiles.config(p, self.ctx.vault)
        except VaultError as e:
            toast(self.page, str(e), error=True)
            return None
        ctxt, _ = redact(await self.context_text())
        system = build_system(self.mode, ctxt, self._server_note())
        api = to_api_messages(self.msgs, lambda t: redact(t)[0])
        if not api:
            return None
        m = Msg("assistant", "", ai=True)
        self.msgs.append(m)
        if self.view:
            self.view.add(m)
        try:
            async for delta in client.stream_chat(cfg, api, system):
                m.text += delta
                if self.view:
                    self.view.stream(m)
        except ProviderError as e:
            if m.text:
                m.text += f"\n\n⚠ {e.message}"
                if self.view:
                    self.view.refresh_msg(m)
            else:
                self.msgs.remove(m)
                if self.view:
                    self.view.remove(m)
            toast(self.page, e.message, error=True)
            return None
        except asyncio.CancelledError:
            m.text += "\n\n_(stopped)_"
            if self.view:
                self.view.refresh_msg(m)
            raise
        if not m.text.strip():
            self.msgs.remove(m)
            if self.view:
                self.view.remove(m)
            toast(self.page, self._t("The model returned an empty reply."), error=True)
            return None
        if self.view:
            self.view.refresh_msg(m)
        self.persist()
        return m.text

    # ---- native tool-calling reply ----
    async def _reply_with_tools(self) -> tuple[Optional[str], list[ToolCall]]:
        """Stream a reply with native tool-calling. Returns (assistant_text, tool_calls).

        On any error, the assistant message is rolled back (matching `_reply`'s behavior)
        and (None, []) is returned so the conversation loop stops.
        """
        p = self.profile()
        if p is None:
            toast(self.page, self._t("Set up an AI profile first."), error=True)
            open_profiles(self.ctx)
            return None, []
        try:
            if p.api_key_enc and not await self.ctx.ensure_unlocked():
                return None, []
            cfg = self.ctx.ai_profiles.config(p, self.ctx.vault)
        except VaultError as e:
            toast(self.page, str(e), error=True)
            return None, []
        ctxt, _ = redact(await self.context_text())
        system = build_system(self.mode, ctxt, self._server_note())
        api = to_api_messages(self.msgs, lambda t: redact(t)[0])
        if not api:
            return None, []
        # If we have pending tool results from a previous turn, splice them in
        # at the end of the message list before sending.
        if self._tool_result_msgs:
            api = api + list(self._tool_result_msgs)
            self._tool_result_msgs = []     # consumed
        m = Msg("assistant", "", ai=True)
        self.msgs.append(m)
        if self.view:
            self.view.add(m)
        calls: list[ToolCall] = []
        try:
            async for ev in client.stream_chat_with_tools(cfg, api, system, tools_enabled=True):
                etype = ev.get("type")
                if etype == "text":
                    m.text += ev.get("delta", "")
                    if self.view:
                        self.view.stream(m)
                elif etype == "tool_calls":
                    calls = ev.get("calls", [])
        except ProviderError as e:
            if m.text:
                m.text += f"\n\n⚠ {e.message}"
                if self.view:
                    self.view.refresh_msg(m)
            else:
                self.msgs.remove(m)
                if self.view:
                    self.view.remove(m)
            toast(self.page, e.message, error=True)
            return None, []
        except asyncio.CancelledError:
            m.text += "\n\n_(stopped)_"
            if self.view:
                self.view.refresh_msg(m)
            raise
        if not m.text.strip() and not calls:
            self.msgs.remove(m)
            if self.view:
                self.view.remove(m)
            toast(self.page, self._t("The model returned an empty reply."), error=True)
            return None, []
        # store the structured tool calls on the Msg so the
        # UI's "Show raw tool calls" toggle can render them. We convert the
        # ToolCall dataclass instances to plain dicts for JSON serialization.
        if calls:
            m.tool_calls = [
                {"id": c.id, "name": c.name, "args": c.args}
                for c in calls
            ]
        if self.view:
            self.view.refresh_msg(m)
        self.persist()
        return m.text, calls

    # ---- commands ----
    async def gate(self, cmd: str, auto_safe: bool) -> bool:
        """Approval rules. Dangerous → typed confirmation always. Safe → auto only if allowed."""
        r = classify(cmd)
        if r.level == "dangerous":
            return await self.ctx.ask_typed("Dangerous command", f"{cmd}\n\n⚠ {r.explanation}", "RUN")
        if auto_safe and not r.needs_approval:
            return True
        title = {"safe": "Run this command?", "writes": "This command changes things",
                 "destructive": "This command deletes or stops things"}[r.level]
        note = "\n\nIt may print secrets; in agent mode the output is sent to the AI (redacted)." if r.sensitive else ""
        return await self.ctx.ask_yes_no(title, f"{cmd}\n\n{r.explanation}{note}", "Run")

    async def execute(self, cmd: str, ai: bool) -> Msg:
        risk = classify(cmd)
        try:
            r = await self.ctx.ssh.run(cmd, timeout=60)
            out = (r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")).strip()
            code = r.exit_code
        except TakError as e:
            out, code = e.message, -1
        a = self.ctx.active
        self.ctx.activity.add(a.id if a else "", a.name if a else "", redact(cmd)[0], code,
                              "ai" if ai else "user", risk.level)
        return Msg("result", out or "(no output)", cmd=cmd, exit=code, ai=ai)

    async def run_clicked(self, cmd: str) -> None:
        if self.ctx.ssh.status.value != "connected":
            toast(self.page, self._t("Not connected."), error=True)
            return
        if await self.gate(cmd, auto_safe=True):
            self._add(await self.execute(cmd, ai=False))

    async def terminal_clicked(self, cmd: str, close: Optional[Callable[[], None]]) -> None:
        if self.ctx.ssh.status.value != "connected":
            toast(self.page, self._t("Not connected."), error=True)
            return
        if not await self.gate(cmd, auto_safe=True):
            return
        await self.ctx.terminal.ensure_session()
        if not (self.ctx.terminal.session and self.ctx.terminal.session.alive):
            toast(self.page, self._t("Couldn't open a terminal session."), error=True)
            return
        a = self.ctx.active
        self.ctx.activity.add(a.id if a else "", a.name if a else "", redact(cmd)[0], None, "user",
                              classify(cmd).level)
        self.ctx.terminal.send(cmd.replace("\n", "\r") + "\r")
        if close:
            close()
        self.ctx.navigate("terminal")

    def clear(self) -> None:
        def do() -> None:
            self.msgs = []
            self.ctx.chats.clear(self.conn_id or "none")
            if self.view:
                self.view.rebuild()
        confirm(self.page, "Clear this chat?", "The history for this server will be deleted from this device.",
                do, yes_label=self._t("Clear"), lang=self._lang)

    async def quick(self, prompt: Optional[str]) -> None:
        if prompt is None:                                         # "What does this command do?"
            cmd = (self.view.input.value or "").strip() if self.view else ""
            if not cmd:
                for m in reversed(self.msgs):
                    cmd = first_command(m.text) if m.role == "assistant" else ""
                    if cmd:
                        break
            if not cmd:
                toast(self.page, self._t("Type or paste a command in the box first."))
                return
            await self.send("What does this command do? Explain each part and flag any risks:\n\n"
                            f"```bash\n{cmd}\n```")
            if self.view:
                self.view.set_input("")
            return
        tail = self.ctx.terminal.tail(60)
        if not tail.strip():
            self.prefill(prompt + "\n\n```\n(paste the text here)\n```")
            toast(self.page, self._t("No terminal output yet — paste the text into the message."))
            return
        await self.send(f"{prompt}\n\n```\n{tail}\n```")


# ====================================================================== view
class ChatView:
    def __init__(self, ctl: ChatController, compact: bool, on_close: Optional[Callable[[], None]]) -> None:
        self.ctl, self.page, self.compact, self.on_close = ctl, ctl.page, compact, on_close
        # Persian translation helper.
        self._lang = ctl.ctx.store.data.language
        self._t = lambda s: translate(s, self._lang)  # noqa: E731
        self.widgets: dict[int, ft.Container] = {}
        self._pending: set[int] = set()
        self.list = ft.ListView(expand=True, spacing=10, auto_scroll=True, padding=8)
        self.privacy = ft.Text("", size=11, opacity=0.7)
        self.usage = ft.Text("", size=11, opacity=0.6)
        self.input = ft.TextField(hint_text=self._t("Ask about your server…"), multiline=True, min_lines=1, max_lines=4,
                                  expand=True, border_radius=12)
        self.send_btn = ft.IconButton(ft.Icons.SEND, on_click=lambda _: self._send(), width=48, height=48)
        self.profile_dd = ft.Dropdown(expand=True, on_select=self._on_profile, label=self._t("Model profile"))
        self.mode_dd = ft.Dropdown(expand=True, label=self._t("Mode"), value=ctl.mode, on_select=self._on_mode,
                                   options=[ft.DropdownOption(key=k, text=v) for k, v in MODES.items()])

        menu = ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content=ft.Text(self._t("What will be sent?")), icon=ft.Icons.VISIBILITY,
                             on_click=lambda _: self.page.run_task(self._preview)),
            ft.PopupMenuItem(content=ft.Text(self._t("Shared context…")), icon=ft.Icons.TUNE,
                             on_click=lambda _: self._context_dialog()),
            ft.PopupMenuItem(content=ft.Text(self._t("AI profiles…")), icon=ft.Icons.SMART_TOY,
                             on_click=lambda _: open_profiles(ctl.ctx, self.refresh_profiles)),
            ft.PopupMenuItem(content=ft.Text(self._t("Export chat")), icon=ft.Icons.IOS_SHARE,
                             on_click=lambda _: self._export()),
            ft.PopupMenuItem(content=ft.Text(self._t("Clear chat")), icon=ft.Icons.DELETE_SWEEP,
                             on_click=lambda _: ctl.clear())])
        head = [ft.Text(self._t("AI assistant"), size=16, weight=ft.FontWeight.W_600, expand=True), menu]
        if compact and on_close:
            head.append(ft.IconButton(ft.Icons.CLOSE, tooltip=self._t("Close"), width=44, height=44,
                                      on_click=lambda _: on_close()))
        chips = ft.Row([ft.TextButton(self._t(label), on_click=lambda _, p=prompt: self.page.run_task(ctl.quick, p))
                        for label, prompt in QUICK], scroll=ft.ScrollMode.AUTO, spacing=0)
        self.control = ft.Column([
            ft.Row(head, scroll=ft.ScrollMode.AUTO, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([self.profile_dd, self.mode_dd], scroll=ft.ScrollMode.AUTO, spacing=8),
            self.privacy,
            ft.Container(self.list, expand=True),
            chips, self.usage,
            ft.Row([self.input, self.send_btn], scroll=ft.ScrollMode.AUTO,
                   vertical_alignment=ft.CrossAxisAlignment.END)],
            expand=True, spacing=6)
        self.refresh_profiles(update=False)
        self.rebuild(update=False)
        if ctl.pending_prefill:
            self.input.value, ctl.pending_prefill = ctl.pending_prefill, None
        self.set_running(ctl.running, update=False)

    # ---- header ----
    def refresh_profiles(self, update: bool = True) -> None:
        ctl = self.ctl
        lst = ctl.ctx.ai_profiles.list()
        self.profile_dd.options = [ft.DropdownOption(key=p.id, text=f"{p.name} · {p.model}") for p in lst]
        cur = ctl.profile()
        self.profile_dd.value = cur.id if cur else None
        if cur is None:
            self.privacy.value = self._t("No AI profile yet — open the menu → AI profiles… to add one.")
        elif is_local_url(cur.base_url):
            self.privacy.value = self._t("Local model: your messages stay on your network.")
        else:
            host = urlparse(cur.base_url or DEFAULT_URLS[cur.kind]).netloc
            self.privacy.value = self._t(
                "Cloud provider ({host}) receives your messages and any shared context "
                "such as terminal output. Secrets are redacted first.").format(host=host)
        if update:
            self.page.update()

    def _on_profile(self, e) -> None:
        self.ctl.s.ai_active_profile = e.control.value
        self.ctl.ctx.store.save()
        self.refresh_profiles()

    def _on_mode(self, e) -> None:
        self.ctl.mode = e.control.value
        self.ctl.persist()
        if self.ctl.mode != "suggest":
            toast(self.page, "In this mode the AI can ask to run commands. You approve them as the mode requires; "
                             "dangerous ones always need typed confirmation.")

    # ---- messages ----
    def rebuild(self, update: bool = True) -> None:
        self.widgets.clear()
        self.list.controls = []
        if not self.ctl.msgs:
            self.list.controls.append(ft.Container(padding=20, content=ft.Text(
                self._t("Ask anything about this server, or use a quick action below. Suggested commands show their risk level before you run them."), opacity=0.7)))
        for m in self.ctl.msgs:
            self._append(m)
        self._update_usage()
        if update:
            self.page.update()

    def _append(self, m: Msg) -> None:
        box = ft.Container(content=self._content(m), padding=10, border_radius=theme.RADIUS,
                           bgcolor=self._bg(m), margin=ft.Margin.only(left=40 if m.role == "user" else 0,
                                                                      right=0 if m.role == "user" else 16))
        self.widgets[id(m)] = box
        self.list.controls.append(box)

    @staticmethod
    def _bg(m: Msg):
        return {"user": ft.Colors.PRIMARY_CONTAINER, "assistant": ft.Colors.SURFACE_CONTAINER_HIGH,
                "result": ft.Colors.SURFACE_CONTAINER}[m.role]

    def add(self, m: Msg) -> None:
        if self.list.controls and not self.widgets:
            self.list.controls.clear()                              # drop the empty-state hint
        self._append(m)
        self._update_usage()
        self.page.update()

    def remove(self, m: Msg) -> None:
        w = self.widgets.pop(id(m), None)
        if w in self.list.controls:
            self.list.controls.remove(w)
        self.page.update()

    def note(self, text: str) -> None:
        self.list.controls.append(ft.Text(text, size=11, opacity=0.6, italic=True))
        self.page.update()

    def refresh_msg(self, m: Msg) -> None:
        if (w := self.widgets.get(id(m))) is not None:
            w.content = self._content(m)
        self._update_usage()
        self.page.update()

    def stream(self, m: Msg) -> None:
        if id(m) in self._pending:
            return
        self._pending.add(id(m))
        self.page.run_task(self._flush, m)

    async def _flush(self, m: Msg) -> None:
        await asyncio.sleep(0.12)
        self._pending.discard(id(m))
        self.refresh_msg(m)

    def _update_usage(self) -> None:
        n = sum(estimate_tokens(m.text) for m in self.ctl.msgs)
        self.usage.value = f"~{n:,} tokens in this chat (estimate)"

    def _content(self, m: Msg) -> ft.Control:
        hint = ft.Text(f"~{estimate_tokens(m.text)} tokens", size=10, opacity=0.5)
        if m.role == "user":
            return ft.Column([ft.Text(m.text, selectable=True), hint], spacing=2)
        if m.role == "result":
            return self._result(m)
        parts: list[ft.Control] = []
        for b in split_blocks(m.text):
            parts.append(self._code(b) if b.kind == "code" else
                         ft.Markdown(b.content, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB))
        if not parts:
            parts.append(ft.ProgressRing(width=18, height=18, stroke_width=2))
        # if the setting is on and this message used native
        # tool-calling, append a collapsible block with the raw tool-call payloads.
        if m.tool_calls and getattr(self.ctl.s, "ai_show_tool_calls", False):
            parts.append(self._tool_calls_block(m))
        # add a "Branch from here" button to assistant messages
        # that have messages after them (i.e. there's something to discard).
        msg_index = self.ctl.msgs.index(m) if m in self.ctl.msgs else -1
        if msg_index >= 0 and self._can_branch(msg_index):
            parts.append(ft.Row([
                hint,
                ft.TextButton(self._t("Branch from here"), icon=ft.Icons.CALL_SPLIT, height=32,
                               on_click=lambda _, i=msg_index: self._branch(i)),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))
        else:
            parts.append(hint)
        return ft.Column(parts, spacing=6)

    # ---------------------------------------------------------------- branching
    def _can_branch(self, index: int) -> bool:
        """True if branching at this index makes sense."""
        from services.chat_branch import can_branch
        return can_branch(self.ctl.msgs, index)

    def _branch(self, index: int) -> None:
        """Truncate the conversation to messages[0..index], discarding the rest."""
        from services.chat_branch import branch_from, branch_summary
        msgs = self.ctl.msgs
        summary = branch_summary(msgs, index)
        last_msg = msgs[index].text[:60] + "…" if len(msgs[index].text) > 60 else msgs[index].text

        def do_branch() -> None:
            try:
                self.ctl.msgs = branch_from(msgs, index)
                self.ctl.persist()
                self.rebuild()
                toast(self.page, f"Branched: {summary}")
            except IndexError as e:
                toast(self.page, str(e), error=True)

        confirm(self.page, self._t("Branch from here?"),
                f"This keeps everything up to and including this reply:\n\n"
                f"\"{last_msg}\"\n\n{summary}.\n"
                f"The discarded messages are removed from this chat.",
                do_branch, yes_label=self._t("Branch"), lang=self._lang)

    def _tool_calls_block(self, m: Msg) -> ft.Control:
        """a collapsible block showing the raw tool-call payloads."""
        from services.tool_inspect import format_tool_calls, summary
        formatted = format_tool_calls(m.tool_calls)
        badge = summary(m)
        return ft.ExpansionTile(
            title=ft.Row([
                ft.Icon(ft.Icons.CODE, size=16, opacity=0.7),
                ft.Text(f"Tool calls · {badge}", size=12, weight=ft.FontWeight.W_600, opacity=0.8),
            ], spacing=6),
            controls=[
                ft.Container(
                    padding=ft.Padding.all(8),
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                    border_radius=theme.RADIUS,
                    content=ft.Text(formatted, font_family="monospace", size=10,
                                     selectable=True, no_wrap=False))
            ],
            initially_expanded=False)

    def _result(self, m: Msg) -> ft.Control:
        lines = m.text.splitlines()
        shown = "\n".join(lines[:14]) + (f"\n… ({len(lines) - 14} more lines)" if len(lines) > 14 else "")
        who = "AI ran" if m.ai else "You ran"
        status = "declined" if m.exit is None else f"exit {m.exit}"
        return ft.Column([
            ft.Row([ft.Icon(ft.Icons.TERMINAL, size=16), ft.Text(f"{who} · {status}", size=12,
                                                                  weight=ft.FontWeight.W_600,
                                                                  color=ft.Colors.ERROR if m.exit not in (0, None) else None)]),
            ft.Text(m.cmd, font_family="monospace", size=11, selectable=True, opacity=0.8),
            ft.Text(shown, font_family="monospace", size=11, selectable=True)], spacing=4)

    def _code(self, b) -> ft.Control:
        ctl = self.ctl
        rows: list[ft.Control] = [
            ft.Row([ft.Text(b.lang or "code", size=11, opacity=0.6, expand=True),
                    ft.IconButton(ft.Icons.COPY, icon_size=18, width=40, height=40, tooltip=self._t("Copy"),
                                  on_click=lambda _, t=b.content: self.page.run_task(ctl.ctx.terminal.copy_text, t))],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(b.content, font_family="monospace", size=12, selectable=True)]
        if b.is_command:
            r = classify(b.content)
            cmd = b.content.strip()
            rows.append(ft.Row([
                ft.Container(ft.Text(r.level.upper(), size=10, weight=ft.FontWeight.W_700, color=ft.Colors.WHITE),
                             bgcolor=RISK_COLORS[r.level], padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                             border_radius=4),
                ft.Text(r.explanation + ("  ·  may print secrets" if r.sensitive else ""), size=11, expand=True)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER))
            rows.append(ft.Row([
                ft.FilledTonalButton(self._t("Run"), icon=ft.Icons.PLAY_ARROW, height=40,
                                     on_click=lambda _, c=cmd: self.page.run_task(ctl.run_clicked, c)),
                ft.TextButton(self._t("Terminal"), icon=ft.Icons.TERMINAL, height=40,
                              on_click=lambda _, c=cmd: self.page.run_task(ctl.terminal_clicked, c, self.on_close)),
                ft.TextButton(self._t("Snippet"), icon=ft.Icons.BOOKMARK_ADD, height=40,
                              on_click=lambda _, c=cmd: ctl.ctx.save_snippet(c))],
                scroll=ft.ScrollMode.AUTO, spacing=0))
        return ft.Container(ft.Column(rows, spacing=4), padding=8, border_radius=8,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST)

    # ---- composer ----
    def set_input(self, text: str) -> None:
        self.input.value = text
        self.page.update()

    def set_running(self, running: bool, update: bool = True) -> None:
        self.send_btn.icon = ft.Icons.STOP_CIRCLE if running else ft.Icons.SEND
        self.send_btn.tooltip = self._t("Stop") if running else self._t("Send")
        self.send_btn.on_click = (lambda _: self.ctl.stop()) if running else (lambda _: self._send())
        if update:
            self.page.update()

    def _send(self) -> None:
        text = self.input.value or ""
        if not text.strip():
            return
        self.input.value = ""
        self.page.update()
        self.page.run_task(self.ctl.send, text)

    # ---- dialogs ----
    async def _preview(self) -> None:
        text, n = await self.ctl.preview()
        self.page.show_dialog(ft.AlertDialog(
                        title=ft.Text(self._t("What will be sent")), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([
                ft.Text(f"{n} secret(s) masked before sending." if n else "No secrets detected.", size=12),
                ft.Text(text, size=11, font_family="monospace", selectable=True)], tight=True)),
            actions=[ft.TextButton(self._t("Close"), on_click=lambda _: self.page.pop_dialog())]))

    def _export(self) -> None:
        self.page.show_dialog(ft.AlertDialog(
            scrollable=True,
            title=ft.Text(self._t("Export chat")), content=ft.Column([
                ft.Text(self._t("Long-press to select and copy. This is the unredacted local history."), size=12),
                ft.TextField(value=export_text(self.ctl.msgs), multiline=True, min_lines=6, max_lines=10,
                             read_only=True)], tight=True, width=340),
            actions=[ft.TextButton(self._t("Close"), on_click=lambda _: self.page.pop_dialog())]))

    def _context_dialog(self) -> None:
        c = self.ctl.s.ai_context

        def sw(key: str, label: str) -> ft.Control:
            def ch(e):
                c[key] = e.control.value
                self.ctl.ctx.store.save()
            return ft.Switch(label=label, value=c.get(key, False), on_change=ch)

        def lines(e):
            c["term"] = int(e.control.value)
            self.ctl.ctx.store.save()

        self.page.show_dialog(ft.AlertDialog(
                        title=ft.Text(self._t("Shared context")), scrollable=True,
            content=ft.Container(width=340, content=ft.Column([
                ft.Text(self._t("Choose what the AI can see alongside your messages. Secrets are always redacted."),
                        size=12),
                sw("os", "OS / distro / kernel"), sw("shell", "Shell"), sw("cwd", "File-manager folder"),
                sw("pkg", "Package manager"), sw("services", "Running services (runs one command)"),
                ft.Dropdown(label=self._t("Last terminal lines"), value=str(c.get("term", 0)), on_select=lines,
                            options=[ft.DropdownOption(key=str(n), text="Off" if n == 0 else f"{n} lines")
                                     for n in (0, 20, 50, 100)])], tight=True)),
            actions=[ft.TextButton(self._t("Done"), on_click=lambda _: self.page.pop_dialog())]))


# ====================================================================== profile manager
def open_profiles(ctx: AppContext, on_change: Optional[Callable[[], None]] = None) -> None:
    page = ctx.page
    lang = ctx.store.data.language
    _t = lambda s: translate(s, lang)  # noqa: E731
    col = ft.ListView(spacing=6, expand=True)

    def changed() -> None:
        draw()
        if on_change:
            on_change()
        page.update()

    def draw() -> None:
        active = ctx.chat.profile() if ctx.chat else None
        rows = []
        for p in ctx.ai_profiles.list():
            def use(_, p=p):
                ctx.store.data.ai_active_profile = p.id
                ctx.store.save()
                changed()

            def delete(_, p=p):
                confirm(page, _t("Delete profile?"), f"“{p.name}” and its saved key will be removed.",
                        lambda: (ctx.ai_profiles.delete(p.id), changed()), yes_label=_t("Delete"), lang=lang)
            rows.append(ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE if active and active.id == p.id else ft.Icons.CIRCLE_OUTLINED, size=18),
                ft.Column([ft.Text(p.name, weight=ft.FontWeight.W_600),
                           ft.Text(f"{KINDS[p.kind]} · {p.model}", size=11, opacity=0.7)], spacing=0, expand=True),
                ft.IconButton(ft.Icons.CHECK, tooltip=_t("Use"), width=44, height=44, on_click=use),
                ft.IconButton(ft.Icons.EDIT, tooltip=_t("Edit"), width=44, height=44,
                              on_click=lambda _, p=p: open_profile_editor(ctx, p, changed)),
                ft.IconButton(ft.Icons.DELETE, tooltip=_t("Delete"), width=44, height=44, on_click=delete)]))
        col.controls = rows or [ft.Text(_t("No profiles yet. Add one to start chatting."), opacity=0.7)]

    draw()
    page.show_dialog(ft.AlertDialog(
        scrollable=True,
        title=ft.Text(_t("AI profiles")), content=ft.Container(width=340, height=320, content=col),
        actions=[ft.TextButton(_t("Add profile"), icon=ft.Icons.ADD,
                               on_click=lambda _: open_profile_editor(ctx, None, changed)),
                 ft.TextButton(_t("Close"), on_click=lambda _: page.pop_dialog())]))


def open_profile_editor(ctx: AppContext, existing: Optional[AIProfile], done: Callable[[], None]) -> None:
    page = ctx.page
    lang = ctx.store.data.language
    _t = lambda s: translate(s, lang)  # noqa: E731
    p = existing or AIProfile(name="", kind="openai")
    name = ft.TextField(label=_t("Name"), value=p.name)
    kind = ft.Dropdown(label=_t("Provider type"), value=p.kind,
                       options=[ft.DropdownOption(key=k, text=v) for k, v in KINDS.items()])
    base = ft.TextField(label=_t("Base URL"), value=p.base_url, hint_text=DEFAULT_URLS[p.kind])
    key = ft.TextField(label=_t("API key"), password=True, can_reveal_password=True,
                       hint_text=_t("Leave blank to keep the saved key") if p.api_key_enc else "Paste your key")
    no_key = ft.Switch(label=_t("No API key (local model)"), value=bool(existing and not existing.api_key_enc))
    model = ft.TextField(label=_t("Model"), value=p.model)
    models_dd = ft.Dropdown(label=_t("Available models"), visible=False,
                            on_select=lambda e: (setattr(model, "value", e.control.value), page.update()))
    temp = ft.Slider(min=0, max=2, divisions=20, value=p.temperature, label="{value}")
    max_tok = ft.TextField(label=_t("Max tokens"), value=str(p.max_tokens), keyboard_type=ft.KeyboardType.NUMBER)
    headers = ft.TextField(label=_t("Custom headers (one “Name: value” per line)"), multiline=True, min_lines=2,
                           max_lines=4, value="\n".join(f"{k}: {v}" for k, v in p.headers.items()))
    result = ft.Text("", size=12)

    def on_kind(e):
        base.hint_text = DEFAULT_URLS[e.control.value]
        page.update()
    kind.on_select = on_kind

    def collect() -> tuple[AIProfile, Optional[str]]:
        hdrs = {}
        for line in (headers.value or "").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip():
                    hdrs[k.strip()] = v.strip()
        try:
            mt = int(max_tok.value)
        except ValueError:
            mt = 0
        prof = replace(p, name=name.value.strip(), kind=kind.value, base_url=base.value.strip(),
                       model=model.value.strip(), temperature=round(temp.value, 1), max_tokens=mt, headers=hdrs)
        k = "" if no_key.value else (key.value if key.value else None)
        return prof, k

    async def config():
        prof, k = collect()
        if k is None and prof.api_key_enc and not await ctx.ensure_unlocked():
            return None
        return ctx.ai_profiles.config(prof, ctx.vault, key_override=k)

    async def fetch(_):
        try:
            cfg = await config()
            if cfg is None:
                return
            result.value, result.color = "Fetching models…", None
            page.update()
            names = await client.list_models(cfg)
        except (ValueError, VaultError, ProviderError) as e:
            result.value, result.color = f"✗ {getattr(e, 'message', None) or e}", ft.Colors.ERROR
            page.update()
            return
        models_dd.options = [ft.DropdownOption(key=n, text=n) for n in names]
        models_dd.visible = bool(names)
        result.value, result.color = f"{len(names)} model(s) found.", ft.Colors.GREEN
        page.update()

    async def test(_):
        try:
            cfg = await config()
            if cfg is None:
                return
            result.value, result.color = "Testing…", None
            page.update()
            msg = await client.test_connection(cfg)
        except (ValueError, VaultError, ProviderError) as e:
            result.value, result.color = f"✗ {getattr(e, 'message', None) or e}", ft.Colors.ERROR
            page.update()
            return
        result.value, result.color = "✓ " + msg, ft.Colors.GREEN
        page.update()

    async def save(_):
        prof, k = collect()
        try:
            ctx.ai_profiles.validate(prof)
            if k and not await ctx.ensure_unlocked():
                return
            ctx.ai_profiles.save(prof, k, ctx.vault)
        except (ValueError, VaultError) as e:
            toast(page, str(e), error=True)
            return
        if not ctx.store.data.ai_active_profile:
            ctx.store.data.ai_active_profile = prof.id
            ctx.store.save()
        page.pop_dialog()
        done()

    page.show_dialog(ft.AlertDialog(
                title=ft.Text(_t("Edit profile") if existing else _t("New profile")), scrollable=True,
        content=ft.Container(width=340, content=ft.Column([
            name, kind, base, key, no_key, model,
            ft.Row([ft.TextButton(_t("Fetch models"), icon=ft.Icons.CLOUD_DOWNLOAD, on_click=fetch)]), models_dd,
            ft.Text(_t("Temperature"), size=12), temp, max_tok, headers,
            ft.Text("Local servers (Ollama, LM Studio, vLLM, llama.cpp) work with plain http:// LAN URLs "
                    "and no key.", size=11, opacity=0.7), result], tight=True, spacing=theme.SPACE)),
        actions=[ft.TextButton(_t("Test"), on_click=test),
                 ft.TextButton(_t("Cancel"), on_click=lambda _: page.pop_dialog()),
                 ft.FilledButton(_t("Save"), on_click=save)]))
