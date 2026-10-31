import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from core.models import RemoteEntry, parse_longname
from services import files as F
from services.transfers import (CANCELLED, DONE, FAILED, REMOTE_PART, TransferQueue,
                                unique_local_path)


def E(name, d=False, size=0, mtime=0, perms=0o644):
    return RemoteEntry(name=name, path="/x/" + name, is_dir=d, size=size, mtime=mtime, perms=perms)


class PathTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(F.parent("/"), "/")
        self.assertEqual(F.parent("/home"), "/")
        self.assertEqual(F.parent("/home/ubuntu/"), "/home")
        self.assertEqual(F.join("/", "a"), "/a")
        self.assertEqual(F.breadcrumbs("/"), [("/", "/")])
        self.assertEqual(F.breadcrumbs("/a/b"), [("/", "/"), ("a", "/a"), ("b", "/a/b")])
        self.assertEqual(F.normalize("/a/../b//c"), "/b/c")

    def test_names(self):
        for bad in ("", "  ", ".", "..", "a/b"):
            self.assertIsNotNone(F.valid_name(bad), bad)
        self.assertIsNone(F.valid_name("my file (1).txt"))
        self.assertIsNone(F.valid_name("ünï.txt"))

    def test_subpath_and_protected(self):
        self.assertTrue(F.is_subpath("/a/b/c", "/a/b"))
        self.assertTrue(F.is_subpath("/a/b", "/a/b"))
        self.assertFalse(F.is_subpath("/a/bc", "/a/b"))
        for p in ("/", "/etc", "/etc/", "/usr", "/home/ubuntu"):
            self.assertTrue(F.is_protected(p, "/home/ubuntu"), p)
        self.assertFalse(F.is_protected("/home/ubuntu/projects", "/home/ubuntu"))
        self.assertFalse(F.is_protected("/etc/nginx", "/home/ubuntu"))


class ListTests(unittest.TestCase):
    ents = [E("b.txt", size=5, mtime=2), E("A.py", size=50, mtime=1), E("zdir", d=True),
            E("adir", d=True), E(".hidden", size=1, mtime=3), E("c.md", size=10, mtime=9)]

    def names(self, xs):
        return [e.name for e in xs]

    def test_sort(self):
        self.assertEqual(self.names(F.sort_entries(self.ents, "name")),
                         ["adir", "zdir", ".hidden", "A.py", "b.txt", "c.md"])
        self.assertEqual(self.names(F.sort_entries(self.ents, "size"))[2:], [".hidden", "b.txt", "c.md", "A.py"])
        self.assertEqual(self.names(F.sort_entries(self.ents, "date", True))[2:], ["c.md", ".hidden", "b.txt", "A.py"])
        self.assertEqual(self.names(F.sort_entries(self.ents, "type"))[2:], [".hidden", "c.md", "A.py", "b.txt"])
        self.assertTrue(all(e.is_dir for e in F.sort_entries(self.ents, "date", True)[:2]))  # dirs first even reversed

    def test_filter(self):
        self.assertNotIn(".hidden", self.names(F.filter_entries(self.ents)))
        self.assertIn(".hidden", self.names(F.filter_entries(self.ents, show_hidden=True)))
        self.assertEqual(self.names(F.filter_entries(self.ents, "PY")), ["A.py"])


class MiscTests(unittest.TestCase):
    def test_perms(self):
        self.assertEqual(F.perms_string(0o755, True), "drwxr-xr-x")
        self.assertEqual(F.perms_string(0o644), "-rw-r--r--")
        self.assertEqual(F.perms_string(0o4755), "-rwsr-xr-x")
        self.assertEqual(F.perms_string(0o1777, True), "drwxrwxrwt")
        self.assertEqual(F.perms_octal(0o755), "0755")

    def test_du_and_longname(self):
        self.assertEqual(F.parse_du_kb("1234\t/var/log\n"), 1234 * 1024)
        self.assertIsNone(F.parse_du_kb(""))
        self.assertEqual(parse_longname("drwxr-xr-x    2 root     root         4096 Oct 14 08:00 my dir"), ("root", "root"))
        self.assertEqual(parse_longname(None), ("", ""))

    def test_editable(self):
        self.assertEqual(F.check_editable(100, b"hello")[0], "ok")
        self.assertEqual(F.check_editable(100, b"\x7fELF\x00\x01")[0], "refuse")
        self.assertEqual(F.check_editable(2_000_000, b"text")[0], "warn")
        self.assertEqual(F.check_editable(9_000_000, b"text")[0], "refuse")
        self.assertEqual(F.check_editable(10, "héllo".encode("latin-1"))[0], "warn")
        self.assertEqual(F.check_editable(10, "é".encode()[:1])[0], "ok")   # cut multibyte tail

    def test_unique_local(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(unique_local_path(d, "a.txt").name, "a.txt")
            (d / "a.txt").write_text("x")
            self.assertEqual(unique_local_path(d, "a.txt").name, "a (1).txt")
            (d / "a (1).txt").write_text("x")
            self.assertEqual(unique_local_path(d, "a.txt").name, "a (2).txt")


class FakeSSH:
    def __init__(self, delay=0.0, fail=None):
        self.delay, self.fail = delay, fail
        self.uploaded, self.replaced, self.removed, self.order = {}, [], [], []

    async def download(self, remote, local, progress):
        self.order.append(remote)
        for i in range(1, 5):
            await asyncio.sleep(self.delay)
            progress(i * 25, 100)
        Path(local).write_text("data")
        if self.fail:
            raise RuntimeError(self.fail)

    async def upload(self, local, remote, progress):
        self.order.append(remote)
        for i in range(1, 5):
            await asyncio.sleep(self.delay)
            progress(i * 25, 100)
        self.uploaded[remote] = True
        if self.fail:
            raise RuntimeError(self.fail)

    async def replace(self, src, dst):
        self.replaced.append((src, dst))

    async def remove(self, path):
        self.removed.append(path)


def spawn(fn):
    return asyncio.ensure_future(fn())


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def wait(self, q, secs=2.0):
        end = asyncio.get_event_loop().time() + secs
        while q.active_count and asyncio.get_event_loop().time() < end:
            await asyncio.sleep(0.01)

    async def test_download_ok_is_atomic_and_sequential(self):
        with tempfile.TemporaryDirectory() as d:
            ssh = FakeSSH()
            q = TransferQueue(ssh, spawn, notify_interval=0)
            a = q.add_download("/r/a", os.path.join(d, "a"), "a", 100)
            b = q.add_download("/r/b", os.path.join(d, "b"), "b", 100)
            await self.wait(q)
            self.assertEqual((a.status, b.status), (DONE, DONE))
            self.assertEqual(ssh.order, ["/r/a", "/r/b"])
            self.assertEqual(sorted(os.listdir(d)), ["a", "b"])        # no .part left

    async def test_failed_download_cleans_part(self):
        with tempfile.TemporaryDirectory() as d:
            q = TransferQueue(FakeSSH(fail="disk full"), spawn, notify_interval=0)
            t = q.add_download("/r/a", os.path.join(d, "a"), "a")
            await self.wait(q)
            self.assertEqual(t.status, FAILED)
            self.assertEqual(t.error, "disk full")
            self.assertEqual(os.listdir(d), [])

    async def test_cancel_running_and_queued(self):
        with tempfile.TemporaryDirectory() as d:
            q = TransferQueue(FakeSSH(delay=0.05), spawn, notify_interval=0)
            a = q.add_download("/r/a", os.path.join(d, "a"), "a")
            b = q.add_download("/r/b", os.path.join(d, "b"), "b")
            c = q.add_download("/r/c", os.path.join(d, "c"), "c")
            await asyncio.sleep(0.06)
            q.cancel(a.id)        # running
            q.cancel(b.id)        # queued
            await self.wait(q)
            self.assertEqual((a.status, b.status, c.status), (CANCELLED, CANCELLED, DONE))
            self.assertEqual(sorted(os.listdir(d)), ["c"])

    async def test_upload_uses_temp_name_then_replaces(self):
        ssh = FakeSSH()
        q = TransferQueue(ssh, spawn, notify_interval=0)
        t = q.add_upload("/local/f", "/srv/f", "f", 100)
        await self.wait(q)
        self.assertEqual(t.status, DONE)
        self.assertEqual(ssh.replaced, [("/srv/f" + REMOTE_PART, "/srv/f")])

    async def test_failed_upload_removes_temp(self):
        ssh = FakeSSH(fail="boom")
        q = TransferQueue(ssh, spawn, notify_interval=0)
        t = q.add_upload("/local/f", "/srv/f", "f")
        await self.wait(q)
        self.assertEqual(t.status, FAILED)
        self.assertEqual(ssh.removed, ["/srv/f" + REMOTE_PART])

    async def test_clear_finished_keeps_active(self):
        with tempfile.TemporaryDirectory() as d:
            q = TransferQueue(FakeSSH(), spawn, notify_interval=0)
            q.add_download("/r/a", os.path.join(d, "a"), "a")
            await self.wait(q)
            q.clear_finished()
            self.assertEqual(q.items, [])


if __name__ == "__main__":
    unittest.main()
