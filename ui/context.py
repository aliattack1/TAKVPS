"""AppContext: wires storage, SSH and dialogs together for the UI."""
from __future__ import annotations

import asyncio
import inspect
import shlex
from dataclasses import dataclass, field
from typing import Callable, Optional

import flet as ft

from core.errors import SftpError, TakError
from core.events import CONNECTION_STATUS, EventBus
from core.models import Credentials
from core.ssh_client import SSHClient, Status
from services import sysinfo as sysinfo_svc
from services.alerts import Alert, AlertRunner
from services.monitor import MonitorPoller, MonitorState, Thresholds
from services.transfers import TransferQueue
from services.transfer_history import (TransferHistory, history_path, load as load_history,
                                       save as save_history, record_finished, dedupe_against_queue)
from storage.activity import ActivityLog
from storage.applock import AppLockStore
from storage.ai_profiles import AIProfileStore
from storage.chats import ChatStore
from storage.dbprofiles import DbProfileStore
from storage.snippets import RunHistory, SnippetStore
from services.redact import redact
from services.risk import LEVELS, classify
from services.runner import WRONG_SUDO
from core.tunnels import TunnelManager
from services.snippets import new_id
from services.tunnels import FavoritesStore
from storage.connections import ConnectionConfigError, ConnectionProfile, ConnectionStore
from storage.settings import SettingsStore, data_dir
from storage.vault import Vault, VaultError
from .components.common import toast

SYSINFO_EVENT = "sysinfo"


@dataclass
class FilesState:
    """File-manager state shared by the tab, the side panel and the phone dialog."""
    path: str = ""
    cache: dict = field(default_factory=dict)       # path -> list[RemoteEntry]
    clip: list = field(default_factory=list)         # paths copied/cut
    clip_mode: str = "copy"                          # "copy" | "move"


class AppContext:
    def __init__(self, page: ft.Page, store: SettingsStore) -> None:
        self.page = page
        self.store = store
        self.vault = Vault(data_dir())
        self.conns = ConnectionStore(data_dir() / "connections.json")
        self.bus = EventBus()
        self.ssh = SSHClient(self.bus, store.data.known_hosts, self.trust, self._persist_hosts)
        self.active: Optional[ConnectionProfile] = None
        self.sysinfo: Optional[sysinfo_svc.SysInfo] = None
        # monitoring: state/history live here so they survive page rebuilds
        self.monitor = MonitorState()
        self.monitor_view = None
        self.poller = MonitorPoller(
            self.ssh, self.monitor, lambda: self.store.data.monitor_interval,
            self._monitor_update, self._monitor_error, page.run_task)
        self.bus.subscribe(CONNECTION_STATUS, self._on_status)
        self.applock = AppLockStore(data_dir() / "applock.json")
        self.alert_history: list[Alert] = []
        self.env_path = ""                                      # set by the Files page to open the .env editor
        self.tunnels = TunnelManager(self.ssh)
        self.db_profiles = DbProfileStore(data_dir() / "db_profiles.json")
        self.tunnel_favs = FavoritesStore(data_dir() / "tunnels.json")
        self.index: dict[str, list] = {}                       # names seen this session, for the command palette
        self.fleet: dict = {}                                  # last multi-server results
        self.palette_pages: Callable[[], list[tuple[str, str]]] = lambda: []
        self.system_notify: Callable[[str, str], bool] = lambda title, body: False   # hook: OS notifications
        # Phase 18: badge counts for nav icons.
        self._alert_count: int = 0
        self._activity_count: int = 0
        self.alerts = AlertRunner(self.ssh, lambda: float(store.data.alert_interval), self._thresholds,
                                  lambda: list(store.data.alert_services), self._on_alert, page.run_task)
        self.ai_profiles = AIProfileStore(data_dir() / "ai_profiles.json")
        self.chats = ChatStore(data_dir() / "chats")
        self.activity = ActivityLog(data_dir() / "activity.jsonl")
        self.snippets = SnippetStore(data_dir() / "snippets.json")
        self.snippet_runs = RunHistory(data_dir() / "snippet_runs.jsonl")
        self._sudo_pw: Optional[str] = None                # memory only, cleared on (dis)connect
        self.page_hooks: dict[str, Callable[[str], None]] = {}   # called with the visible page key
        self.chat = None                                   # set by the shell
        self.open_chat: Callable[..., None] = lambda prefill=None: None
        self.save_snippet: Callable[[str], None] = lambda cmd: None       # set by the shell
        self.files_state = FilesState()
        self.transfers = TransferQueue(self.ssh, page.run_task, self._transfers_changed)
        self.transfers_view = None
        # Phase 13.5: persistent transfer history — survives app restarts.
        self.transfer_history: TransferHistory = load_history(history_path(data_dir()))
        self.open_editor: Callable[[str], None] = lambda path: toast(
            self.page, "The code editor arrives in Phase 5.")
        # set by the shell
        self.navigate: Callable[[str], None] = lambda key: None
        self.refresh_ui: Callable[[], None] = lambda: None

    def _transfers_changed(self) -> None:
        # Phase 13.5: persist any newly-finished transfers to the history file.
        try:
            new_done = [t for t in self.transfers.items
                        if t.status in ("done", "failed", "cancelled")
                        and not getattr(t, "_hist_recorded", False)]
            for t in new_done:
                record_finished(t, self.transfer_history)
                t._hist_recorded = True     # type: ignore[attr-defined]
            if new_done:
                save_history(history_path(data_dir()), self.transfer_history)
        except Exception:  # noqa: BLE001
            # History is best-effort; never break the UI over a failed write.
            pass
        if self.transfers_view:
            self.transfers_view.refresh()
        # Phase 15: tick the file manager's transfer status bar.
        # The file manager lives in the FilesState; we find it via page_hooks
        # or just call a ctx-stored callback.
        cb = getattr(self, "_files_transfer_tick", None)
        if cb:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def _monitor_update(self, m) -> None:
        if self.monitor_view:
            self.monitor_view.apply(m)

    def _monitor_error(self, msg: str) -> None:
        if self.monitor_view:
            self.monitor_view.set_status(f"Paused: {msg}")

    def _thresholds(self) -> Thresholds:
        s = self.store.data
        return Thresholds(tuple(s.thr_cpu), tuple(s.thr_ram), tuple(s.thr_disk))

    def _on_alert(self, a: Alert) -> None:
        self.alert_history.insert(0, a)
        del self.alert_history[50:]
        self._alert_count += 1     # Phase 18: increment badge
        self.notify(a.title, a.detail)

    def notify(self, title: str, body: str) -> None:
        """In-app notification (always). OS-level notifications need a plugin Flet doesn't ship by default;
        `system_notify` is the hook for it."""
        toast(self.page, f"⚠ {title}: {body}", error=True)
        try:
            self.system_notify(title, body)
        except Exception:  # noqa: BLE001
            pass

    def sync_alerts(self) -> None:
        self.alerts.set_active(bool(self.store.data.alerts_enabled))

    async def safe_confirm(self, title: str, detail: str = "") -> bool:
        """Safe mode: ask before every write. Without Safe mode this is a no-op that returns True."""
        if not self.store.data.safe_mode:
            return True
        return await self.ask_yes_no(f"Safe mode: {title}", detail or "Safe mode asks before every change.", "Continue")

    def _on_status(self, st) -> None:
        if st is Status.CONNECTED:
            self.monitor.reset()          # new session: counters/history restart
        else:
            self.tunnels.stop_all()       # forwards belong to the old connection

    def _persist_hosts(self, known: dict) -> None:
        self.store.data.known_hosts = known
        self.store.save()

    # ---------- dialogs ----------
    async def trust(self, host: str, fingerprint: str, changed_from: Optional[str]) -> bool:
        """Host-key prompt. A changed key gets a loud, red warning."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        def done(v: bool) -> None:
            self.page.pop_dialog()
            if not fut.done():
                fut.set_result(v)

        changed = changed_from is not None
        mono = "monospace"
        body: list[ft.Control] = [ft.Text(host, weight=ft.FontWeight.W_600),
                                  ft.Text("SHA256 fingerprint", size=12, opacity=0.7),
                                  ft.Text(fingerprint, selectable=True, font_family=mono, size=12)]
        if changed:
            body = [ft.Text("The key for this server is DIFFERENT from the one you trusted. "
                            "This could mean the server was reinstalled — or that someone is "
                            "intercepting your connection. Do not continue unless you expect this.",
                            color=ft.Colors.ERROR, weight=ft.FontWeight.W_600),
                    ft.Text("Previously trusted", size=12, opacity=0.7),
                    ft.Text(changed_from, selectable=True, font_family=mono, size=12)] + body
        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("⚠ HOST KEY CHANGED" if changed else "Trust this server?"),
            content=ft.Column(body, tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("Reject", on_click=lambda _: done(False)),
                     ft.FilledButton("Trust anyway" if changed else "Trust",
                                     bgcolor=ft.Colors.ERROR if changed else None,
                                     on_click=lambda _: done(True))]))
        return await fut

    async def ask_text(self, title: str, label: str, password: bool = False,
                       ok: str = "OK", value: str = "") -> Optional[str]:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        field = ft.TextField(label=label, password=password, can_reveal_password=password,
                             autofocus=True, value=value)

        def done(v: Optional[str]) -> None:
            self.page.pop_dialog()
            if not fut.done():
                fut.set_result(v)

        field.on_submit = lambda _: done(field.value)
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(title), content=field,
            actions=[ft.TextButton("Cancel", on_click=lambda _: done(None)),
                     ft.FilledButton(ok, on_click=lambda _: done(field.value))]))
        return await fut

    async def ask_yes_no(self, title: str, body: str, yes: str = "Yes") -> bool:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        def done(v: bool) -> None:
            self.page.pop_dialog()
            if not fut.done():
                fut.set_result(v)
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(title), content=ft.Text(body),
            actions=[ft.TextButton("Cancel", on_click=lambda _: done(False)),
                     ft.FilledButton(yes, on_click=lambda _: done(True))]))
        return await fut

    async def ask_typed(self, title: str, body: str, word: str) -> bool:
        """Typed confirmation: the user must type `word` to proceed."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        field = ft.TextField(hint_text=f'Type "{word}" to continue', autofocus=True)
        ok = ft.FilledButton("Confirm", bgcolor=ft.Colors.ERROR, disabled=True)

        def done(v: bool) -> None:
            self.page.pop_dialog()
            if not fut.done():
                fut.set_result(v)

        def changed(_):
            ok.disabled = (field.value or "").strip() != word
            self.page.update()

        field.on_change = changed
        ok.on_click = lambda _: done(True)
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(title, color=ft.Colors.ERROR),
            content=ft.Column([ft.Text(body, selectable=True), field], tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("Cancel", on_click=lambda _: done(False)), ok]))
        return await fut

    # ---------- sudo / risk helpers ----------
    async def get_sudo(self) -> tuple[bool, Optional[str]]:
        """(ok, password). No password needed for root/passwordless sudo. Asked once per connection and
        kept in memory only; reusing the saved connection password is the user's explicit choice."""
        info = self.sysinfo
        if info and info.sudo in ("root", "passwordless"):
            return True, None
        if self._sudo_pw:
            return True, self._sudo_pw
        if info and info.sudo == "none":
            toast(self.page, "This user has no sudo access on the server.", error=True)
            return False, None
        a = self.active
        if a and a.auth == "password" and a.password_enc and await self.ensure_unlocked():
            if await self.ask_yes_no("Sudo password", "Use this server's saved connection password for sudo "
                                     "(kept in memory for this session only)?", "Use it"):
                try:
                    self._sudo_pw = self.vault.decrypt(a.password_enc)
                    return True, self._sudo_pw
                except VaultError:
                    pass
        pw = await self.ask_text("Sudo password", f"Password for {a.username if a else 'sudo'}",
                                 password=True, ok="Continue")
        if pw is None:
            return False, None
        self._sudo_pw = pw
        return True, pw

    async def run_privileged(self, cmd: str, timeout: float = 60, input_text: Optional[str] = None,
                             log_as: Optional[str] = None):
        """Run `cmd` as root (plain for root users, sudo otherwise). None if the user cancels sudo."""
        info = self.sysinfo
        if info and info.sudo == "root":
            r = await self.ssh.run(cmd, timeout=timeout, input_text=input_text)
            self.log_action(log_as or cmd, r.exit_code)
            return r
        ok, pw = await self.get_sudo()
        if not ok:
            return None
        r = await self.ssh.run(cmd, timeout=timeout, sudo=True, sudo_password=pw, input_text=input_text)
        if WRONG_SUDO.search(r.stderr + r.stdout):
            self.forget_sudo()
        self.log_action(log_as or cmd, r.exit_code)
        return r

    async def open_url(self, url: str) -> None:
        try:
            r = self.page.launch_url(url)
            if inspect.isawaitable(r):
                await r
        except Exception:  # noqa: BLE001
            toast(self.page, "Couldn't open the browser.", error=True)

    def log_action(self, cmd: str, exit_code, source: str = "action") -> None:
        """Record a state-changing command in the local activity log (secrets redacted)."""
        a = self.active
        try:
            self.activity.add(a.id if a else "", a.name if a else "", redact(cmd)[0], exit_code, source,
                              classify(cmd).level)
            self._activity_count += 1     # Phase 18: increment badge
        except OSError:
            pass

    async def write_privileged(self, path: str, data: bytes, mode: Optional[int] = None,
                               owner: str = "root", group: str = "root") -> None:
        """Write a root-owned file: upload to /tmp, then `sudo install` it into place."""
        info = self.sysinfo
        if info and info.sudo == "root":
            await self.ssh.write_file(path, data, keep_perms=True)
            return
        if mode is None:
            try:
                mode = ((await self.ssh.stat(path)).permissions or 0o644) & 0o7777
            except TakError:
                mode = 0o644
        tmp = f"/tmp/.takvps-{new_id()}"
        await self.ssh.write_file(tmp, data, keep_perms=False)
        try:
            r = await self.run_privileged(
                f"install -m {mode:o} -o {shlex.quote(owner)} -g {shlex.quote(group)} -- {shlex.quote(tmp)} {shlex.quote(path)}")
            if r is None:
                raise SftpError("Cancelled.")
            if not r.ok:
                raise SftpError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "Could not write the file.")
        finally:
            try:
                await self.ssh.remove(tmp)
            except TakError:
                pass

    def forget_sudo(self) -> None:
        self._sudo_pw = None

    async def gate_risk(self, title: str, commands: list[str], force_confirm: bool = False) -> bool:
        """Confirm according to the worst risk: dangerous → typed, destructive → confirm,
        writes → confirm only in Safe mode. Safe → no prompt."""
        risks = [(c, classify(c)) for c in commands if c.strip()]
        if not risks:
            return True
        cmd, worst = max(risks, key=lambda x: LEVELS[x[1].level])
        shown = cmd if len(cmd) < 400 else cmd[:400] + "…"
        if worst.level == "dangerous":
            return await self.ask_typed("Dangerous command", f"{title}\n\n{shown}\n\n⚠ {worst.explanation}", "RUN")
        if worst.level == "destructive" or force_confirm or (worst.level == "writes" and self.store.data.safe_mode):
            return await self.ask_yes_no(title, f"{shown}\n\n{worst.explanation}", "Run")
        return True

    async def ensure_unlocked(self) -> bool:
        while self.vault.locked:
            pw = await self.ask_text("Unlock TAKVPS", "Passphrase", password=True, ok="Unlock")
            if pw is None:
                return False
            # scrypt is CPU-heavy: keep it off the UI thread
            if not await asyncio.to_thread(self.vault.unlock, pw):
                toast(self.page, "Wrong passphrase.", error=True)
        return True

    async def fill_password(self, creds: Credentials, auth: str) -> bool:
        """If password auth has no saved password, ask once (never stored)."""
        if auth == "password" and not creds.password:
            pw = await self.ask_text("Password", f"Password for {creds.username}@{creds.host}",
                                     password=True, ok="Connect")
            if pw is None:
                return False
            creds.password = pw
        return True

    # ---------- connection ----------
    async def connect(self, p: ConnectionProfile) -> bool:
        try:
            if not await self.ensure_unlocked():
                return False
            creds = self.conns.credentials(p, self.vault)
            if not await self.fill_password(creds, p.auth):
                return False
            await self.ssh.connect(creds)
        except TakError as e:
            toast(self.page, e.message, error=True)
            return False
        except (ConnectionConfigError, VaultError) as e:
            toast(self.page, str(e), error=True)
            return False
        self.active = p
        self.files_state = FilesState()       # new server: start at its home folder
        self._sudo_pw = None
        self.conns.touch(p.id)
        self.store.data.last_connection_id = p.id
        self.store.save()
        await self.refresh_sysinfo()
        self.sync_alerts()
        return True

    async def disconnect(self) -> None:
        self._sudo_pw = None
        await self.ssh.disconnect()
        self.sysinfo = None
        await self.bus.publish(SYSINFO_EVENT, None)

    async def reconnect(self) -> None:
        if self.active:
            await self.connect(self.active)
        else:
            self.navigate("servers")

    async def refresh_sysinfo(self) -> None:
        try:
            self.sysinfo = await sysinfo_svc.fetch(self.ssh)
        except TakError as e:
            self.sysinfo = None
            toast(self.page, e.message, error=True)
        await self.bus.publish(SYSINFO_EVENT, self.sysinfo)

    async def test(self, creds: Credentials) -> tuple[bool, str]:
        """Reachability + auth check on a throwaway client (nothing is saved)."""
        tmp = SSHClient(EventBus(), self.store.data.known_hosts, self.trust, self._persist_hosts)
        try:
            await tmp.connect(creds)
            r = await tmp.run("id -un", timeout=10)
            await tmp.disconnect()
            return True, f"Connected as {r.stdout.strip() or creds.username}"
        except TakError as e:
            return False, e.message
