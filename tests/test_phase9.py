import unittest
from datetime import datetime

from services import cron as C
from services import packages as P
from services import systemd as SD

UNITS = """\
  ssh.service                 loaded    active   running OpenBSD Secure Shell server
● foo.service                 loaded    failed   failed  Foo daemon
  cron.service                loaded    active   running Regular background program processing daemon
  old.service                 masked    inactive dead    old.service
##FILES
ssh.service     enabled  enabled
foo.service     enabled  enabled
cron.service    enabled  enabled
old.service     masked   enabled
getty@.service  enabled  enabled
apport.service  disabled enabled
"""


class SystemdTests(unittest.TestCase):
    def test_parse_units(self):
        us = SD.parse_units(UNITS)
        names = [u.name for u in us]
        self.assertEqual(names[0], "foo.service")                       # failed first
        self.assertNotIn("getty@.service", names)                       # template skipped
        d = {u.name: u for u in us}
        self.assertEqual((d["ssh.service"].active, d["ssh.service"].enabled), ("active", "enabled"))
        self.assertEqual(d["foo.service"].description, "Foo daemon")
        self.assertTrue(d["foo.service"].failed)
        self.assertEqual(d["apport.service"].enabled, "disabled")      # only in unit-files
        self.assertEqual(d["old.service"].enabled, "masked")
        self.assertEqual(d["ssh.service"].short, "ssh")

    def test_commands_validate(self):
        self.assertEqual(SD.action_command("restart", "nginx.service"), "systemctl restart nginx.service")
        for bad in ("a b", "x;rm -rf /", "$(id)", ""):
            with self.assertRaises(ValueError):
                SD.action_command("start", bad)
        with self.assertRaises(ValueError):
            SD.action_command("poweroff", "x.service")
        self.assertIn("-f", SD.journal_command("x.service", follow=True))
        self.assertIn("-n 5000", SD.journal_command("x.service", lines=99999))
        self.assertEqual(SD.unit_path("my app"), None)
        self.assertEqual(SD.unit_path("web"), "/etc/systemd/system/web.service")
        self.assertTrue(SD.needs_journal_sudo("Hint: You are currently not seeing messages from other users"))

    def test_timers(self):
        t = SD.parse_timers("Mon 2026-10-19 00:00:00 UTC 2 days left Sun 2026-10-18 00:00:01 UTC 21h ago "
                            "logrotate.timer logrotate.service")
        self.assertEqual(t[0]["unit"], "logrotate.timer")
        self.assertEqual(t[0]["activates"], "logrotate.service")


class CronTests(unittest.TestCase):
    def test_validate(self):
        for ok in ("* * * * *", "*/5 0-6 1,15 jan-mar mon-fri", "0 3 * * 7", "@daily", "@reboot", "5/10 * * * *"):
            self.assertIsNone(C.validate_schedule(ok), ok)
        for bad in ("* * * *", "60 * * * *", "* 24 * * *", "*/0 * * * *", "a b c d e", "@sometimes", "5-1 * * * *"):
            self.assertIsNotNone(C.validate_schedule(bad), bad)

    def test_describe(self):
        d = C.describe
        self.assertEqual(d("*/5 * * * *"), "Every 5 minutes")
        self.assertEqual(d("30 * * * *"), "Every hour at minute 30")
        self.assertEqual(d("30 2 * * *"), "Every day at 02:30")
        self.assertEqual(d("0 9 * * 1-5"), "Every weekday (Mon–Fri) at 09:00")
        self.assertEqual(d("0 9 * * 0,6"), "Every Sunday, Saturday at 09:00")
        self.assertEqual(d("15 4 10 * *"), "On day 10 of every month at 04:15")
        self.assertEqual(d("@reboot"), "At system boot")
        self.assertEqual(d("* * * * *"), "Every minute")

    def test_next_runs(self):
        now = datetime(2026, 10, 16, 12, 30)                              # a Friday
        self.assertEqual(C.next_runs("*/15 * * * *", now, 3),
                         [datetime(2026, 10, 16, 12, 45), datetime(2026, 10, 16, 13, 0), datetime(2026, 10, 16, 13, 15)])
        self.assertEqual(C.next_runs("0 9 * * 1", now, 2), [datetime(2026, 10, 19, 9, 0), datetime(2026, 10, 26, 9, 0)])
        self.assertEqual(C.next_runs("0 0 1 * *", now, 1), [datetime(2026, 11, 1, 0, 0)])
        self.assertEqual(C.next_runs("0 0 29 2 *", now, 1), [datetime(2028, 2, 29, 0, 0)])   # leap year
        self.assertEqual(C.next_runs("@reboot", now), [])
        # dom and dow both restricted => either matches (cron semantics)
        r = C.next_runs("0 0 13 * 5", now, 2)
        self.assertEqual(r[0], datetime(2026, 10, 23, 0, 0))

    def test_build(self):
        self.assertEqual(C.build_schedule("minutes", n=10), "*/10 * * * *")
        self.assertEqual(C.build_schedule("daily", hour=3, minute=5), "5 3 * * *")
        self.assertEqual(C.build_schedule("weekly", dow=1, hour=9, minute=0), "0 9 * * 1")
        self.assertEqual(C.build_schedule("monthly", dom=1, hour=0, minute=0), "0 0 1 * *")
        self.assertEqual(C.build_schedule("custom", expr=" 1 2 3 4 5 "), "1 2 3 4 5")

    def test_crontab_roundtrip_preserves_untouched_lines(self):
        text = "# my jobs\nMAILTO=me@x.com\n\n*/5 * * * * /usr/bin/a --x\n#takvps-disabled# 0 3 * * * /b\n# 0 4 * * * /c\n@reboot /d\n"
        lines = C.parse_crontab(text)
        jobs = [l for l in lines if l.kind == "job"]
        self.assertEqual([(j.schedule, j.command, j.enabled) for j in jobs],
                         [("*/5 * * * *", "/usr/bin/a --x", True), ("0 3 * * *", "/b", False), ("@reboot", "/d", True)])
        self.assertEqual(C.serialize(lines), text)                          # untouched lines survive exactly
        jobs[1].raw = C.make_job(jobs[1].schedule, jobs[1].command, True).raw
        self.assertIn("\n0 3 * * * /b\n", C.serialize(lines))
        self.assertNotIn("MAILTO", "".join(l.raw for l in lines if l.kind == "job"))

    def test_validate_job(self):
        self.assertIsNone(C.validate_job("* * * * *", "echo hi"))
        self.assertIsNotNone(C.validate_job("* * * * *", " "))
        self.assertIsNotNone(C.validate_job("* * * * *", "a\nb"))
        self.assertIsNotNone(C.validate_job("bad", "x"))


class PackageTests(unittest.TestCase):
    def test_detect_and_names(self):
        self.assertEqual(P.detect("apt-get"), "apt")
        self.assertIsNone(P.detect("emerge"))
        self.assertEqual(P.cmd_install("apt", ["nginx", "curl"]), "DEBIAN_FRONTEND=noninteractive apt-get install -y nginx curl")
        for bad in (["a;b"], ["$(x)"], [], ["a b"]):
            with self.assertRaises(ValueError):
                P.cmd_remove("dnf", bad)
        for pm in P.SUPPORTED:
            for f in (P.cmd_installed, P.cmd_upgrade_all, P.cmd_refresh, P.script_upgradable):
                self.assertTrue(f(pm))
            self.assertIn("'", P.cmd_search(pm, "a b")) if pm != "x" else None

    def test_parse_apt(self):
        inst = P.parse_installed("apt", "nginx\t1.18.0-6\nbash\t5.1-6\n")
        self.assertEqual([p.name for p in inst], ["bash", "nginx"])
        up = P.parse_upgradable("apt", "Listing...\n"
                                "openssl/jammy-updates,jammy-security 3.0.2-0ubuntu1.15 amd64 [upgradable from: 3.0.2-0ubuntu1.14]\n"
                                "vim/jammy-updates 2:8.2 amd64 [upgradable from: 2:8.1]\n")
        self.assertEqual([(u.name, u.security) for u in up], [("openssl", True), ("vim", False)])
        self.assertEqual(up[0].old, "3.0.2-0ubuntu1.14")
        s = P.parse_search("apt", "nginx - small, powerful web server\nnginx-common - common files\n")
        self.assertEqual((s[0].name, s[0].desc), ("nginx", "small, powerful web server"))
        self.assertIn("nginx", P.cmd_security("apt", ["nginx"]))

    def test_parse_other_managers(self):
        up = P.parse_upgradable("dnf", "kernel.x86_64   5.14.1   baseos\nbash.x86_64  5.2   appstream\n##SEC\nkernel.x86_64 5.14.1 baseos\n")
        self.assertEqual([(u.name, u.security) for u in up], [("kernel", True), ("bash", False)])
        self.assertEqual(P.parse_upgradable("pacman", "linux 6.1-1 -> 6.2-1\n")[0].new, "6.2-1")
        self.assertEqual(P.parse_upgradable("apk", "busybox-1.35.0-r1 < 1.36.0-r0\n")[0].name, "busybox")
        self.assertEqual(P.parse_installed("apk", "busybox-1.35.0-r1\nmusl-1.2.3-r4\n")[0].version, "1.35.0-r1")
        self.assertEqual(P.parse_installed("pacman", "bash 5.2\n")[0].version, "5.2")
        ps = P.parse_search("pacman", "extra/nginx 1.24-1\n    HTTP server\n")
        self.assertEqual((ps[0].name, ps[0].desc), ("nginx", "HTTP server"))
        z = P.parse_upgradable("zypper", "v | repo | curl | 7.1 | 7.2 | x86_64\n")
        self.assertEqual(z[0].name, "curl")
        self.assertFalse(P.supports_security_split("pacman"))

    def test_hints_and_reboot(self):
        self.assertIn("Wait", P.friendly_hint("E: Could not get lock /var/lib/dpkg/lock-frontend"))
        self.assertIn("dpkg --configure", P.friendly_hint("E: dpkg was interrupted, you must manually run"))
        self.assertIsNone(P.friendly_hint("all good"))
        self.assertTrue(P.parse_reboot("yes\n"))
        self.assertFalse(P.parse_reboot("no"))
        self.assertIsNone(P.parse_reboot("unknown"))
        self.assertIsNone(P.cmd_changelog("dnf", "x"))


if __name__ == "__main__":
    unittest.main()
