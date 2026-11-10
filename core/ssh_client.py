"""SSH layer built on asyncssh (exec, stream, PTY, SFTP, reconnect, keepalive)."""
from __future__ import annotations

import asyncio
import enum
import shlex
import stat as _st
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional

import asyncssh

from .errors import HostKeyRejected, NotConnectedError, SftpError, TakError, translate
from .events import CONNECTION_STATUS, EventBus
from .models import Credentials, RemoteEntry, parse_longname


class Status(str, enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# (host:port, fingerprint, previously_trusted_fingerprint_or_None) -> trust?
TrustCallback = Callable[[str, str, Optional[str]], Awaitable[bool]]
KnownHosts = dict


class PtyShell:
    """Interactive PTY channel."""

    def __init__(self, proc: asyncssh.SSHClientProcess) -> None:
        self._proc = proc

    async def read(self, n: int = 4096) -> str:
        return await self._proc.stdout.read(n)

    def write(self, data: str) -> None:
        self._proc.stdin.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self._proc.channel.change_terminal_size(cols, rows)

    def close(self) -> None:
        self._proc.close()


class SSHClient:
    def __init__(self, bus: EventBus, known_hosts: KnownHosts, trust_cb: TrustCallback,
                 on_known_hosts_changed: Callable[[KnownHosts], None] = lambda _k: None) -> None:
        self.bus = bus
        self.known_hosts = known_hosts
        self.trust_cb = trust_cb
        self._persist = on_known_hosts_changed
        self.status = Status.DISCONNECTED
        self.creds: Optional[Credentials] = None
        self.home: str = "/"
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._sftp: Optional[asyncssh.SFTPClient] = None
        self._watch: Optional[asyncio.Task] = None
        self._hops: list[asyncssh.SSHClientConnection] = []   # jump-host connections
        self._want_connected = False

    # ---------- lifecycle ----------
    async def _set(self, status: Status) -> None:
        self.status = status
        await self.bus.publish(CONNECTION_STATUS, status)

    async def connect(self, creds: Credentials) -> None:
        if self.status is not Status.DISCONNECTED:
            await self.disconnect()
        self.creds = creds
        self._want_connected = True
        await self._set(Status.CONNECTING)
        try:
            await self._open()
        except BaseException as e:  # noqa: BLE001
            self._want_connected = False
            await self._set(Status.DISCONNECTED)
            raise translate(e) from e
        await self._set(Status.CONNECTED)
        self._watch = asyncio.create_task(self._watchdog())

    async def _verify_host(self, c: Credentials, tunnel=None) -> asyncssh.SSHKey:
        """Trust-on-first-use: fingerprint must match the stored one or be approved.

        Note: ``asyncssh.get_server_host_key`` does NOT accept ``login_timeout`` (it
        only takes ``options=SSHClientConnectionOptions(...)``), so we wrap the
        timeout in an options object instead. Passing it as a kwarg raised
        ``TypeError: got an unexpected keyword argument 'login_timeout'`` on every
        fresh-connection attempt and aborted the host-key dialog.
        """
        opts = asyncssh.SSHClientConnectionOptions(login_timeout=15)
        key = await asyncssh.get_server_host_key(c.host, c.port, tunnel=tunnel, options=opts)
        fp = key.get_fingerprint()
        ident = f"{c.host}:{c.port}"
        known = self.known_hosts.get(ident)
        if known != fp:
            if not await self.trust_cb(ident, fp, known):
                raise HostKeyRejected("Host key was not trusted. Connection cancelled.")
            self.known_hosts[ident] = fp
            self._persist(self.known_hosts)
        return key

    async def _dial(self, c: Credentials, hops: list) -> asyncssh.SSHClientConnection:
        """Open one hop, first opening its jump host(s) and tunnelling through them."""
        tunnel = None
        if c.jump:
            tunnel = await self._dial(c.jump, hops)
            hops.append(tunnel)
        key = await self._verify_host(c, tunnel)
        kwargs: dict = dict(
            host=c.host, port=c.port, username=c.username, tunnel=tunnel,
            known_hosts=([key], [], []),        # pin exactly the trusted key
            keepalive_interval=30, keepalive_count_max=3,
            login_timeout=20, agent_path=None,
        )
        if c.private_key:
            kwargs["client_keys"] = [asyncssh.import_private_key(
                c.private_key, passphrase=c.passphrase)]
            kwargs["password"] = None
        else:
            kwargs["password"] = c.password
            kwargs["client_keys"] = None
        return await asyncssh.connect(**kwargs)

    def _close_hops(self) -> None:
        for h in reversed(self._hops):
            h.close()
        self._hops = []

    async def _open(self) -> None:
        assert self.creds is not None
        self._close_hops()
        hops: list = []
        try:
            self._conn = await self._dial(self.creds, hops)
        except BaseException:
            for h in reversed(hops):
                h.close()
            raise
        self._hops = hops
        self._sftp = None
        self.home = await self._detect_home()

    async def _detect_home(self) -> str:
        # Uses self._conn directly: status is still CONNECTING/RECONNECTING here,
        # so the public run()/sftp() (which require CONNECTED) must not be used.
        assert self._conn is not None
        try:
            self._sftp = await self._conn.start_sftp_client()
            return await self._sftp.realpath(".")
        except Exception:  # noqa: BLE001
            try:
                r = await self._conn.run("echo $HOME", check=False, timeout=10)
                return str(r.stdout).strip() or "/"
            except Exception:  # noqa: BLE001
                return "/"

    async def disconnect(self) -> None:
        self._want_connected = False
        if self._watch:
            self._watch.cancel()
            self._watch = None
        if self._conn:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._close_hops()
        self._conn = self._sftp = None
        await self._set(Status.DISCONNECTED)

    async def _watchdog(self) -> None:
        """Detect drops and reconnect with exponential backoff."""
        while self._want_connected:
            try:
                assert self._conn
                await self._conn.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            if not self._want_connected:
                return
            self._sftp = None
            await self._set(Status.RECONNECTING)
            delay = 1.0
            while self._want_connected:
                try:
                    await asyncio.sleep(delay)
                    await self._open()
                    await self._set(Status.CONNECTED)
                    break
                except Exception:  # noqa: BLE001
                    delay = min(delay * 2, 30)

    def _need(self) -> asyncssh.SSHClientConnection:
        if not self._conn or self.status is not Status.CONNECTED:
            raise NotConnectedError("Not connected to a server.")
        return self._conn

    # ---------- exec ----------
    async def run(self, cmd: str, timeout: float = 30, sudo: bool = False,
                  sudo_password: Optional[str] = None, input_text: Optional[str] = None) -> RunResult:
        conn = self._need()
        stdin = None
        if sudo:
            cmd = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
            stdin = (sudo_password + "\n") if sudo_password else None
        if input_text:                       # e.g. a database password, so it never appears in a command line
            stdin = (stdin or "") + input_text
        try:
            r = await conn.run(cmd, input=stdin, timeout=timeout, check=False)
        except BaseException as e:  # noqa: BLE001
            raise translate(e) from e
        return RunResult(str(r.stdout or ""), str(r.stderr or ""), r.exit_status or 0)

    async def stream(self, cmd: str, sudo: bool = False,
                     sudo_password: Optional[str] = None) -> AsyncIterator[str]:
        """Yield merged stdout/stderr chunks live. Cancel the consumer to stop."""
        conn = self._need()
        if sudo:
            cmd = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
        proc = await conn.create_process(cmd, stderr=asyncssh.STDOUT)
        if sudo and sudo_password:
            proc.stdin.write(sudo_password + "\n")
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.close()

    async def open_shell(self, cols: int = 80, rows: int = 24,
                         command: Optional[str] = None) -> PtyShell:
        conn = self._need()
        proc = await conn.create_process(
            command, term_type="xterm-256color", term_size=(cols, rows),
            stderr=asyncssh.STDOUT, encoding="utf-8", errors="replace")
        return PtyShell(proc)

    # ---------- sftp ----------
    async def sftp(self) -> asyncssh.SFTPClient:
        conn = self._need()
        if self._sftp is None:
            self._sftp = await conn.start_sftp_client()
        return self._sftp

    async def _sf(self, coro: Awaitable):
        try:
            return await coro
        except BaseException as e:  # noqa: BLE001
            raise translate(e) from e

    async def listdir(self, path: str) -> list[RemoteEntry]:
        sftp = await self.sftp()
        names = await self._sf(sftp.readdir(path))
        base = path.rstrip("/")
        out: list[RemoteEntry] = []
        for n in names:
            name = n.filename if isinstance(n.filename, str) else n.filename.decode(errors="replace")
            if name in (".", ".."):
                continue
            a = n.attrs
            perms = a.permissions or 0
            owner, group = parse_longname(getattr(n, "longname", None))
            out.append(RemoteEntry(
                name=name, path=f"{base}/{name}", is_dir=_st.S_ISDIR(perms),
                size=a.size or 0, mtime=a.mtime or 0, perms=perms & 0o7777,
                owner=owner or (str(a.uid) if a.uid is not None else ""),
                group=group or (str(a.gid) if a.gid is not None else ""),
                is_link=_st.S_ISLNK(perms)))
        return out

    async def stat(self, path: str):
        return await self._sf((await self.sftp()).stat(path))

    async def mkdir(self, path: str) -> None:
        await self._sf((await self.sftp()).mkdir(path))

    async def remove(self, path: str, recursive: bool = False) -> None:
        sftp = await self.sftp()
        await self._sf(sftp.rmtree(path) if recursive else sftp.remove(path))

    async def rename(self, src: str, dst: str) -> None:
        await self._sf((await self.sftp()).rename(src, dst))

    async def chmod(self, path: str, mode: int) -> None:
        await self._sf((await self.sftp()).chmod(path, mode))

    async def read_file(self, path: str, max_bytes: int = 2_000_000) -> bytes:
        sftp = await self.sftp()
        size = (await self.stat(path)).size or 0
        if size > max_bytes:
            raise TakError(f"File is too large to open ({size} bytes).")
        async with sftp.open(path, "rb") as f:
            return await f.read()

    async def write_file(self, path: str, data: bytes, keep_perms: bool = True) -> None:
        sftp = await self.sftp()
        mode = None
        if keep_perms:
            try:
                mode = (await sftp.stat(path)).permissions
            except Exception:  # noqa: BLE001
                mode = None
        async with sftp.open(path, "wb") as f:
            await f.write(data)
        if mode is not None:
            await sftp.chmod(path, mode & 0o7777)

    async def download(self, remote: str, local: str,
                       progress: Optional[Callable[[int, int], None]] = None) -> None:
        sftp = await self.sftp()
        handler = (lambda s, d, done, total: progress(done, total)) if progress else None
        await self._sf(sftp.get(remote, local, progress_handler=handler))

    async def upload(self, local: str, remote: str,
                     progress: Optional[Callable[[int, int], None]] = None) -> None:
        sftp = await self.sftp()
        handler = (lambda s, d, done, total: progress(done, total)) if progress else None
        await self._sf(sftp.put(local, remote, progress_handler=handler))

    async def run_stream(self, cmd: str, on_chunk, sudo: bool = False,
                         sudo_password: Optional[str] = None, input_text: Optional[str] = None) -> int:
        """Run `cmd`, calling `on_chunk(text)` live (stdout+stderr merged); returns the exit code.
        Cancelling the task asks the remote process to terminate (needs OpenSSH 7.9+ on the server)."""
        import inspect
        conn = self._need()
        if sudo:
            cmd = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
        proc = await conn.create_process(cmd, stderr=asyncssh.STDOUT, encoding="utf-8", errors="replace")
        if sudo and sudo_password:
            proc.stdin.write(sudo_password + "\n")
        if input_text:
            proc.stdin.write(input_text)
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                res = on_chunk(chunk)
                if inspect.isawaitable(res):
                    await res
            r = await proc.wait(check=False)
            return r.exit_status if r.exit_status is not None else -1
        except BaseException:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            proc.close()

    async def forward(self, local_port: int, dest_host: str, dest_port: int):
        """Local port forward bound to 127.0.0.1 only (never exposed to the network)."""
        conn = self._need()
        try:
            return await conn.forward_local_port("127.0.0.1", local_port, dest_host, dest_port)
        except OSError as e:
            raise TakError("Couldn't listen on that local port. Is it already in use? Try 0 (automatic).") from e
        except BaseException as e:  # noqa: BLE001
            raise translate(e) from e

    # ---------- file-manager helpers ----------
    async def exists(self, path: str) -> bool:
        try:
            await (await self.sftp()).stat(path)
            return True
        except asyncssh.SFTPNoSuchFile:
            return False
        except BaseException as e:  # noqa: BLE001
            raise translate(e) from e

    async def read_head(self, path: str, n: int = 4096) -> bytes:
        sftp = await self.sftp()
        async with sftp.open(path, "rb") as f:
            return await self._sf(f.read(n))

    async def replace(self, src: str, dst: str) -> None:
        """Atomically move src over dst (used to finish uploads)."""
        sftp = await self.sftp()
        try:
            await sftp.posix_rename(src, dst)
        except Exception:  # noqa: BLE001  (server lacks the extension)
            try:
                await sftp.remove(dst)
            except Exception:  # noqa: BLE001
                pass
            await self._sf(sftp.rename(src, dst))

    async def copy(self, src: str, dst: str) -> None:
        r = await self.run(f"cp -a -- {shlex.quote(src)} {shlex.quote(dst)}", timeout=600)
        if not r.ok:
            raise SftpError(r.stderr.strip() or "Copy failed.")

    async def move(self, src: str, dst: str) -> None:
        try:
            await self.rename(src, dst)
        except TakError:                      # e.g. across filesystems
            r = await self.run(f"mv -- {shlex.quote(src)} {shlex.quote(dst)}", timeout=600)
            if not r.ok:
                raise SftpError(r.stderr.strip() or "Move failed.")

    async def folder_size(self, path: str) -> str:
        """Raw `du -sk` output; parsed by services.files.parse_du_kb."""
        r = await self.run(f"du -sk -- {shlex.quote(path)} 2>/dev/null | tail -n 1", timeout=120)
        return r.stdout
