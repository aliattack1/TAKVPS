import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from services import alerts as AL
from services import applock as A
from services import backups as B
from services import fleet as FL
from services import palette as PL
from services.monitor import Mount, Thresholds, MonitorState, parse_snapshot
from storage.applock import AppLockStore


def metrics(cpu=10.0, mem=20.0, disk=30, mount="/"):
    from services.monitor import Metrics
    return Metrics(cpu=cpu, cores=[], mem_total_kb=1000, mem_used_kb=int(mem * 10), mem_pct=mem, swap_total_kb=0,
                   swap_used_kb=0, swap_pct=0, load=(0, 0, 0), uptime_s=1, users=0, procs=0, temp_c=None,
                   mounts=[Mount("/dev/sda1", mount, 100, disk, disk)], net={}, net_down=0, net_up=0, disk_read=0,
                   disk_write=0, top_cpu=[], top_mem=[], taken_at=0)


class AppLockTests(unittest.TestCase):
    def test_pin_rules(self):
        for bad in ("123", "123456789", "12a4", "1111", ""):
            self.assertIsNotNone(A.valid_pin(bad), bad)
        self.assertIsNone(A.valid_pin("2580"))
        self.assertEqual([A.lockout_seconds(n) for n in (1, 4, 5, 6, 7, 20)], [0, 0, 30, 60, 120, 3600])

    def test_store_flow(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lock.json"
            s = AppLockStore(p, iterations=1000)
            self.assertFalse(s.enabled)
            self.assertIsNotNone(s.enable("1111"))
            self.assertIsNone(s.enable("2580"))
            self.assertNotIn("2580", p.read_text())                        # PIN never stored
            self.assertTrue(AppLockStore(p, iterations=1000).enabled)       # persisted
            t = 1000.0
            for _ in range(4):
                self.assertEqual(s.check("0000", now=t), (False, 0))
            ok, wait = s.check("0000", now=t)                               # 5th failure => lockout
            self.assertEqual((ok, wait), (False, 30))
            ok, wait = s.check("2580", now=t + 5)                           # correct PIN still refused while locked
            self.assertFalse(ok)
            self.assertGreater(wait, 20)
            self.assertEqual(s.check("2580", now=t + 40), (True, 0))
            self.assertEqual(s.d["failures"], 0)
            self.assertFalse(s.disable("9999"))
            self.assertTrue(s.disable("2580"))
            self.assertFalse(s.enabled)


class AlertTests(unittest.TestCase):
    thr = Thresholds()

    def test_debounce_and_cooldown(self):
        e = AL.AlertEngine(consecutive=2, cooldown=100)
        hot = metrics(cpu=99)
        self.assertEqual(e.evaluate(hot, self.thr, {}, now=0), [])                 # first breach: wait
        fired = e.evaluate(hot, self.thr, {}, now=10)
        self.assertEqual([a.key for a in fired], ["cpu"])
        self.assertEqual(e.evaluate(hot, self.thr, {}, now=20), [])                # cooldown
        self.assertEqual(len(e.evaluate(hot, self.thr, {}, now=200)), 1)           # cooldown over
        e.evaluate(metrics(cpu=5), self.thr, {}, now=300)                          # recovery resets the count
        self.assertEqual(e.evaluate(hot, self.thr, {}, now=1000), [])              # needs 2 in a row again

    def test_sources(self):
        e = AL.AlertEngine(consecutive=1, cooldown=0)
        a = e.evaluate(metrics(cpu=None, mem=95, disk=97, mount="/data"), self.thr, {"nginx": "failed", "ssh": "active"}, now=1)
        self.assertEqual(sorted(x.key for x in a), ["disk:/data", "ram", "svc:nginx"])
        self.assertEqual(e.evaluate(metrics(), self.thr, {"ssh": "active"}, now=2), [])
        self.assertEqual(AL.parse_active(["a", "b", "c"], "active\nfailed\n"), {"a": "active", "b": "failed", "c": "unknown"})


class FakeSSH:
    def __init__(self):
        self.status = type("S", (), {"value": "connected"})()
        self.cmds = []

    async def run(self, cmd, timeout=30, sudo=False, sudo_password=None):
        self.cmds.append(cmd)
        if "is-active" in cmd:
            return type("R", (), {"stdout": "failed\n", "stderr": "", "exit_code": 3})()
        return type("R", (), {"stdout": SNAP, "stderr": "", "exit_code": 0})()


SNAP = """##STAT
cpu  100 0 50 800 50 0 0 0
##MEM
MemTotal:        1000 kB
MemAvailable:      30 kB
##LOAD
0.1 0.1 0.1 1/1 1
##UPTIME
100.0 1.0
##DF
/dev/sda1 100 50 50 50% /
"""


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_fires_alert_for_memory_and_service(self):
        got = []
        r = AL.AlertRunner(FakeSSH(), lambda: 0.01, lambda: Thresholds(), lambda: ["nginx", "bad name;x"], got.append,
                           lambda fn: asyncio.ensure_future(fn()), AL.AlertEngine(consecutive=1, cooldown=999), min_delay=0)
        out = await r.check_once()
        self.assertEqual(sorted(a.key for a in out), ["ram", "svc:nginx"])
        self.assertNotIn("bad name;x", " ".join(r.ssh.cmds))                      # invalid unit names never reach the shell
        r2 = AL.AlertRunner(FakeSSH(), lambda: 0.01, lambda: Thresholds(), lambda: [], got.append,
                            lambda fn: asyncio.ensure_future(fn()), AL.AlertEngine(consecutive=1, cooldown=999), min_delay=0)
        r2.set_active(True)
        await asyncio.sleep(0.1)
        r2.set_active(False)
        await asyncio.sleep(0.05)
        self.assertTrue(got)                       # the background loop delivered the alert
        self.assertFalse(r2._running)


class BackupTests(unittest.TestCase):
    def test_commands(self):
        cmd, path = B.archive_command("/var/www/my site", "/home/u/backups", "Site backup!", 0, ["*.log", "node_modules"])
        self.assertTrue(path.startswith("/home/u/backups/Site-backup-") and path.endswith(".tar.gz"))
        self.assertIn("-C /var/www 'my site'", cmd)
        self.assertIn("--exclude='*.log'", cmd)
        self.assertIn("ls -lh", cmd)
        for src, dest in [("/", "/b"), ("rel", "/b"), ("/a", "rel"), ("/a", "/a/b"), ("/a", "/a")]:
            with self.assertRaises(ValueError, msg=(src, dest)):
                B.archive_command(src, dest, "x", 0, [])
        c = B.cron_command("/srv/app", "/bk", "app", ["*.tmp"], keep_days=7)
        self.assertIn("\\%Y\\%m\\%d", c)
        self.assertNotIn("%Y", c.replace("\\%Y", ""))
        self.assertIn("-mtime +7 -delete", c)
        self.assertNotIn("-delete", B.cron_command("/srv/app", "/bk", "app", [], 0))
        self.assertEqual(B.slug("../../etc passwd"), "etc-passwd")
        self.assertEqual(B.parse_df_avail("Filesystem 1K-blocks Used Available Use% Mounted\n/dev/sda1 100 40 60 40% /"), 60)
        self.assertTrue(B.is_backup_file("a.tar.gz") and not B.is_backup_file("a.txt"))


class FleetTests(unittest.TestCase):
    OUT = ("##HOST\nweb-1\n##LOAD\n2.00 1.0 0.5 1/200 5\n##CORES\n2\n##MEM\nMemTotal: 1000 kB\nMemAvailable: 100 kB\n"
           "##DISK\n/dev/sda1 100 90 10 90% /\n##UP\n86400.5 1\n##FAILED\n1\n##REBOOT\nyes\n")

    def test_parse_and_health(self):
        s = FL.parse_light(self.OUT)
        self.assertEqual((s.ok, s.host, s.cores, s.disk_pct, s.failed, s.reboot), (True, "web-1", 2, 90, 1, True))
        self.assertAlmostEqual(s.cpu_pct, 100.0)
        self.assertAlmostEqual(s.mem_pct, 90.0)
        self.assertEqual(FL.health(s, Thresholds()), "crit")                    # cpu proxy 100% >= 90
        calm = FL.parse_light(self.OUT.replace("2.00", "0.10").replace("90%", "20%").replace("MemAvailable: 100", "MemAvailable: 900")
                              .replace("##FAILED\n1", "##FAILED\n0"))
        self.assertEqual(FL.health(calm, Thresholds()), "ok")
        self.assertEqual(FL.health(None, Thresholds()), "down")
        self.assertFalse(FL.parse_light("garbage").ok)


class PaletteTests(unittest.TestCase):
    items = [PL.Item("page", "Monitor", "live stats", "monitor"), PL.Item("page", "Firewall", "ufw", "firewall"),
             PL.Item("service", "nginx", "service", "nginx"), PL.Item("snippet", "Restart nginx", "systemctl restart", "s1"),
             PL.Item("server", "Production web", "root@1.2.3.4:22", "c1"), PL.Item("file", "nginx.conf", "/etc/nginx/nginx.conf", "/etc/nginx/nginx.conf")]

    def test_fuzzy(self):
        self.assertIsNone(PL.fuzzy_score("xyz", "monitor"))
        self.assertGreater(PL.fuzzy_score("mon", "Monitor"), PL.fuzzy_score("mnr", "Monitor"))
        self.assertGreater(PL.fuzzy_score("nginx", "nginx"), PL.fuzzy_score("nginx", "restart nginx now"))
        self.assertEqual(PL.fuzzy_score("", "anything"), 0)

    def test_search(self):
        self.assertEqual([i.kind for i in PL.search(self.items, "")][:2], ["page", "page"])      # empty: pages first
        r = PL.search(self.items, "nginx")
        self.assertEqual(r[0].title, "nginx")
        self.assertEqual({i.title for i in r}, {"nginx", "Restart nginx", "nginx.conf"})
        self.assertEqual(PL.search(self.items, "fw")[0].title, "Firewall")
        self.assertEqual(PL.search(self.items, "1.2.3.4")[0].kind, "server")                       # matches the subtitle
        self.assertEqual(PL.search(self.items, "zzzz"), [])
        self.assertLessEqual(len(PL.search(self.items, "", limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
