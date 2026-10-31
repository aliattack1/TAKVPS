"""Transfer queue: sequential uploads/downloads, progress, cancel, history.

Downloads write to `<name>.part` then rename; uploads go to a temp name then replace the
target atomically, so a cancelled/failed transfer never leaves a half-written real file.
No UI imports; the SSH object only needs download/upload/replace/remove.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
PART_SUFFIX = ".part"
REMOTE_PART = ".takvps-part"


def unique_local_path(directory: Path, name: str) -> Path:
    """`name`, or `name (1).ext`, `name (2).ext`... whichever is free."""
    p = directory / name
    if not p.exists():
        return p
    stem, ext = os.path.splitext(name)
    i = 1
    while (directory / f"{stem} ({i}){ext}").exists():
        i += 1
    return directory / f"{stem} ({i}){ext}"


@dataclass
class Transfer:
    id: int
    kind: str                  # "down" | "up"
    name: str
    remote: str
    local: str
    total: int = 0
    done: int = 0
    status: str = QUEUED
    error: str = ""
    created: float = field(default_factory=time.time)

    @property
    def fraction(self) -> Optional[float]:
        return (self.done / self.total) if self.total else None

    @property
    def active(self) -> bool:
        return self.status in (QUEUED, RUNNING)


class TransferQueue:
    def __init__(self, ssh: Any, spawn: Callable[[Callable[[], Awaitable]], Any],
                 on_change: Callable[[], Any] = lambda: None, notify_interval: float = 0.25) -> None:
        self.ssh, self._spawn, self.on_change = ssh, spawn, on_change
        self.items: list[Transfer] = []
        self._next = 1
        self._running = False
        self._lock = threading.Lock()
        self._current: Optional[tuple[int, asyncio.Task]] = None
        self._last_notify = 0.0
        self._interval = notify_interval

    # ---- public API ----
    def add_download(self, remote: str, local: str, name: str, size: int = 0) -> Transfer:
        return self._add("down", remote, local, name, size)

    def add_upload(self, local: str, remote: str, name: str, size: int = 0) -> Transfer:
        return self._add("up", remote, local, name, size)

    def cancel(self, tid: int) -> None:
        t = self._get(tid)
        if not t:
            return
        if t.status == QUEUED:
            t.status = CANCELLED
            self._notify(force=True)
        elif t.status == RUNNING and self._current and self._current[0] == tid:
            self._current[1].cancel()

    def clear_finished(self) -> None:
        self.items = [t for t in self.items if t.active]
        self._notify(force=True)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self.items if t.active)

    # ---- internals ----
    def _get(self, tid: int) -> Optional[Transfer]:
        return next((t for t in self.items if t.id == tid), None)

    def _add(self, kind, remote, local, name, size) -> Transfer:
        t = Transfer(self._next, kind, name, remote, local, total=size)
        self._next += 1
        self.items.append(t)
        self._notify(force=True)
        self._ensure()
        return t

    def _ensure(self) -> None:
        with self._lock:
            if self._running or not any(t.status == QUEUED for t in self.items):
                return
            self._running = True
        self._spawn(self._worker)

    def _notify(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_notify < self._interval:
            return
        self._last_notify = now
        res = self.on_change()
        if inspect.isawaitable(res):
            asyncio.ensure_future(res)

    async def _worker(self) -> None:
        try:
            while True:
                t = next((x for x in self.items if x.status == QUEUED), None)
                if t is None:
                    break
                await self._run_one(t)
        finally:
            with self._lock:
                self._running = False
            self._ensure()

    def _progress(self, t: Transfer) -> Callable[[int, int], None]:
        def cb(done: int, total: int) -> None:
            t.done, t.total = done, total or t.total
            self._notify()
        return cb

    async def _transfer(self, t: Transfer) -> None:
        if t.kind == "down":
            part = t.local + PART_SUFFIX
            try:
                await self.ssh.download(t.remote, part, self._progress(t))
                os.replace(part, t.local)
            except BaseException:
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise
        else:
            tmp = t.remote + REMOTE_PART
            try:
                await self.ssh.upload(t.local, tmp, self._progress(t))
                await self.ssh.replace(tmp, t.remote)
            except BaseException:
                try:
                    await asyncio.shield(self.ssh.remove(tmp))
                except Exception:  # noqa: BLE001
                    pass
                raise

    async def _run_one(self, t: Transfer) -> None:
        t.status = RUNNING
        self._notify(force=True)
        task = asyncio.ensure_future(self._transfer(t))
        self._current = (t.id, task)
        try:
            await task
            t.status = DONE
            if t.total:
                t.done = t.total
        except asyncio.CancelledError:
            if task.cancelled():
                t.status = CANCELLED
            else:
                raise                       # the worker itself is being cancelled
        except Exception as e:  # noqa: BLE001
            t.status, t.error = FAILED, getattr(e, "message", None) or str(e) or "Transfer failed"
        finally:
            self._current = None
            self._notify(force=True)
