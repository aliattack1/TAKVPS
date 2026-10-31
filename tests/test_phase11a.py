import base64
import unittest

from services import firewall as F
from services import logs as L
from services import network as NW
from services import processes as PR
from services import users as U

UFW = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere
[ 2] 80,443/tcp                 ALLOW IN    Anywhere
[ 3] 22/tcp (v6)                ALLOW IN    Anywhere (v6)
[ 4] 8080                       DENY IN     203.0.113.9
[ 5] OpenSSH                    ALLOW IN    10.0.0.0/24
"""


class LogTests(unittest.TestCase):
    def test_files_and_commands(self):
        files = L.parse_log_files("syslog\nauth.log\nauth.log.1\nsyslog.2.gz\nnginx/\nwtmp\nkern.log\n")
        self.assertEqual(files, ["auth.log", "kern.log", "syslog"])
        s = L.file_source("auth.log")
        self.assertEqual(L.command(s, 100), "tail -n 100 /var/log/auth.log")
        self.assertIn("-F /var/log/auth.log", s.follow)
        for bad in ("../etc/passwd", "a b", "x;rm", ""):
            with self.assertRaises(ValueError):
                L.file_source(bad)
        self.assertIn("-n 5000", L.command(L.PRESETS[0], 10**6))

    def test_severity_and_filter(self):
        self.assertEqual(L.severity("sshd: Failed password for root"), "error")
        self.assertEqual(L.severity("nginx: warning: conflicting server name"), "warn")
        self.assertEqual(L.severity("Started Session 3"), "")
        lines = ["Error: A", "info b", "ERROR c"]
        self.assertEqual(L.filter_lines(lines, "error"), ["Error: A", "ERROR c"])
        self.assertEqual(L.filter_lines(lines, "ERROR", case=True), ["ERROR c"])
        self.assertEqual(L.filter_lines(lines, r"^info", regex=True), ["info b"])
        with self.assertRaises(ValueError):
            L.filter_lines(lines, "(", regex=True)


class FirewallTests(unittest.TestCase):
    def test_parse_and_covers(self):
        st = F.parse_ufw(UFW)
        self.assertTrue(st.active)
        self.assertEqual(len(st.rules), 5)
        self.assertTrue(st.rules[2].v6)
        self.assertFalse(F.parse_ufw("Status: inactive").active)
        for to, port, exp in [("22/tcp", 22, True), ("80,443/tcp", 443, True), ("1000:2000/tcp", 1500, True),
                              ("OpenSSH", 22, True), ("OpenSSH", 2222, False), ("Anywhere", 9, True),
                              ("8080", 22, False), ("22/tcp (v6)", 22, True)]:
            self.assertEqual(F.covers_port(to, port), exp, (to, port))

    def test_delete_guard_protects_ssh(self):
        st = F.parse_ufw(UFW)
        self.assertIsNotNone(F.ufw_delete_guard(F.parse_ufw("Status: active\n[ 1] 22/tcp   ALLOW IN   Anywhere\n"), 1, 22))
        self.assertIsNone(F.ufw_delete_guard(st, 2, 22))                # unrelated rule
        self.assertIsNone(F.ufw_delete_guard(st, 1, 22))                # OpenSSH rule 5 still covers IPv4
        self.assertIsNotNone(F.ufw_delete_guard(F.parse_ufw("Status: active\n[ 1] 22/tcp (v6)   ALLOW IN   Anywhere (v6)\n"), 1, 22))
        self.assertIsNone(F.ufw_delete_guard(F.parse_ufw("Status: inactive\n[ 1] 22/tcp   ALLOW IN   Anywhere\n"), 1, 22))
        self.assertIn("no longer exists", F.ufw_delete_guard(st, 99, 22))
        self.assertIsNotNone(F.ufw_delete_guard(F.parse_ufw("Status: active\n[ 1] 2222/tcp   ALLOW IN   Anywhere\n"), 1, 2222))

    def test_enable_plan_adds_ssh_rule_first(self):
        self.assertEqual(F.ufw_enable_plan(F.parse_ufw("Status: inactive\n"), 2222), ["ufw allow 2222/tcp", "ufw --force enable"])
        self.assertEqual(F.ufw_enable_plan(F.parse_ufw(UFW), 22), ["ufw --force enable"])

    def test_add_command_validation(self):
        self.assertEqual(F.ufw_add_command("allow", "8080", "tcp", "", 22), "ufw allow 8080/tcp")
        self.assertEqual(F.ufw_add_command("allow", "6000:6100", "any", "", 22), "ufw allow 6000:6100")
        self.assertEqual(F.ufw_add_command("allow", "3306", "tcp", "10.0.0.0/24", 22),
                         "ufw allow from 10.0.0.0/24 to any port 3306 proto tcp")
        for args in [("deny", "22", "tcp", "", 22), ("reject", "20:30", "any", "", 22), ("allow", "0", "tcp", "", 22),
                     ("allow", "70000", "tcp", "", 22), ("allow", "80; reboot", "tcp", "", 22),
                     ("allow", "80", "icmp", "", 22), ("allow", "80", "tcp", "not-an-ip", 22), ("flush", "80", "tcp", "", 22)]:
            with self.assertRaises(ValueError, msg=args):
                F.ufw_add_command(*args)
        self.assertEqual(F.ufw_add_command("deny", "23", "tcp", "", 22), "ufw deny 23/tcp")
        self.assertEqual(F.ufw_delete_command(3), "ufw --force delete 3")

    def test_firewalld(self):
        info = F.parse_firewalld("running", "public (active)\n  target: default\n  services: ssh dhcpv6-client\n  ports: 8080/tcp 53/udp\n")
        self.assertEqual((info.running, info.zone, info.services, info.ports), (True, "public", ["ssh", "dhcpv6-client"], ["8080/tcp", "53/udp"]))
        self.assertIn("--add-port=8080/tcp", F.firewalld_command("add", "port", "8080/tcp", 22))
        self.assertTrue(F.firewalld_command("add", "service", "http", 22).endswith("firewall-cmd --reload"))
        for args in [("remove", "service", "ssh", 22), ("remove", "port", "22/tcp", 22), ("remove", "port", "2000-3000/tcp", 2222),
                     ("add", "port", "80", 22), ("add", "service", "a;b", 22), ("add", "port", "99999/tcp", 22)]:
            with self.assertRaises(ValueError, msg=args):
                F.firewalld_command(*args)
        self.assertIn("--remove-port=22/udp", F.firewalld_command("remove", "port", "22/udp", 22))   # udp doesn't carry SSH
        self.assertEqual(F.detect_backend("iptables\nufw\n"), "ufw")
        self.assertEqual(F.detect_backend("nft\n"), "nft")
        self.assertEqual(F.detect_backend(""), "")


class ProcessTests(unittest.TestCase):
    PS = ("    1 root              0.0  0.1   0 Ss   86400 /sbin/init splash\n"
          "  900 root              0.0  0.2   0 Ss   86000 /usr/sbin/sshd -D\n"
          " 1234 www-data         12.5  1.2   0 S    3600 nginx: worker process\n"
          " 2001 alice             3.0  4.0  10 R    120 /usr/bin/python3 /srv/app.py --port 80\n"
          " 3000 root              0.0  0.0   0 S    50 sshd: alice [priv]\n"
          "  2 root                0.0  0.0   0 S    86400 [kthreadd]\n")

    def test_parse_sort_filter(self):
        ps = PR.parse_ps(self.PS)
        self.assertEqual(len(ps), 6)
        self.assertEqual(ps[3].cmd, "/usr/bin/python3 /srv/app.py --port 80")
        self.assertEqual(PR.sort_procs(ps, "cpu")[0].pid, 1234)
        self.assertEqual(PR.sort_procs(ps, "mem")[0].pid, 2001)
        self.assertEqual([p.pid for p in PR.filter_procs(ps, "alice")], [2001, 3000])
        self.assertEqual([p.pid for p in PR.filter_procs(ps, "1234")], [1234])
        self.assertEqual(ps[3].name, "python3")

    def test_critical_and_commands(self):
        ps = {p.pid: p for p in PR.parse_ps(self.PS)}
        self.assertTrue(PR.is_critical(ps[1]) and PR.is_critical(ps[900]) and PR.is_critical(ps[2]))
        self.assertFalse(PR.is_critical(ps[3000]))              # a session process, not the daemon
        self.assertFalse(PR.is_critical(ps[2001]))
        self.assertEqual(PR.signal_command(2001, "TERM"), "kill -15 2001")
        self.assertEqual(PR.renice_command(2001, 5), "renice -n 5 -p 2001")
        for f, a in [(PR.signal_command, (1, "TERM")), (PR.signal_command, (5, "SEGV")), (PR.renice_command, (5, 25)),
                     (PR.renice_command, (1, 0))]:
            with self.assertRaises(ValueError):
                f(*a)
        self.assertTrue(PR.permission_denied("kill: (1): Operation not permitted"))


class NetworkTests(unittest.TestCase):
    SS = ("Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
          'tcp   LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=1234,fd=6),("nginx",pid=1235,fd=6))\n'
          'tcp   LISTEN 0      4096            [::]:22            [::]:*    users:(("sshd",pid=900,fd=4))\n'
          'tcp   LISTEN 0      4096       127.0.0.1:5432       0.0.0.0:*\n'
          "udp   UNCONN 0      0      127.0.0.53%lo:53         0.0.0.0:*\n"
          'tcp   LISTEN 0      4096         0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=900,fd=3))\n')

    def test_listen(self):
        ps = NW.parse_listen(self.SS)
        by = {(p.proto, p.port, p.addr): p for p in ps}
        self.assertEqual(by[("tcp", 80, "0.0.0.0")].process, "nginx")
        self.assertEqual(by[("tcp", 22, "::")].pid, 900)
        self.assertTrue(by[("tcp", 80, "0.0.0.0")].public)
        self.assertFalse(by[("tcp", 5432, "127.0.0.1")].public)
        self.assertFalse(by[("udp", 53, "127.0.0.53")].public)
        self.assertEqual(len(ps), 5)

    def test_conns_and_tools(self):
        c = NW.parse_conns('0 0 10.0.0.5:22 1.2.3.4:51234 users:(("sshd",pid=1,fd=3))\n0 0 10.0.0.5:22 1.2.3.4:51235\n0 0 10.0.0.5:80 9.9.9.9:4000\n')
        self.assertEqual(NW.top_remotes(c), [("1.2.3.4", 2), ("9.9.9.9", 1)])
        self.assertIn("ping -c 4", NW.ping_command("example.com"))
        self.assertTrue(NW.valid_host("2001:db8::1") and NW.valid_host("a-b.example.com"))
        for bad in ("-oProxy", "a b", "x;rm", "", "$(id)"):
            self.assertFalse(NW.valid_host(bad), bad)
            with self.assertRaises(ValueError):
                NW.dns_command(bad)


class UserTests(unittest.TestCase):
    def key_line(self, comment="me@phone"):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        pub = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
        return f"{pub} {comment}"

    def test_passwd(self):
        users = U.parse_passwd("root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1::/usr/sbin:/usr/sbin/nologin\n"
                               "alice:x:1000:1000:Alice,,,:/home/alice:/bin/bash\nbob:x:1001:1001::/home/bob:/bin/false\n",
                               "sudo:x:27:alice\n")
        self.assertEqual([u.name for u in users if u.human], ["root", "alice"])
        self.assertTrue(next(u for u in users if u.name == "alice").sudo)
        self.assertEqual(next(u for u in users if u.name == "alice").gecos, "Alice")
        self.assertTrue(U.valid_username("alice") and not U.valid_username("a b") and not U.valid_username("-x"))

    def test_keys(self):
        line = self.key_line()
        text = f"# my keys\n{line}\ncommand=\"/bin/date\",no-pty {self.key_line('restricted')}\ngarbage line\n"
        lines = U.parse_authorized_keys(text)
        self.assertEqual([l.is_key for l in lines], [False, True, True, False])
        self.assertTrue(lines[1].fingerprint.startswith("SHA256:") and "=" not in lines[1].fingerprint)
        self.assertEqual(lines[2].options, 'command="/bin/date",no-pty')
        self.assertEqual(lines[2].comment, "restricted")
        self.assertEqual(U.serialize(lines), text)                        # untouched text preserved
        self.assertEqual(U.key_count(lines), 2)
        k, err = U.validate_new_key(self.key_line("new"), lines)
        self.assertTrue(k and not err)
        self.assertIn("already", U.validate_new_key(line, lines)[1])
        self.assertIn("private", U.validate_new_key("-----BEGIN OPENSSH PRIVATE KEY-----", lines)[1].lower() + "private")
        for bad in ("hello", "ssh-ed25519 AAAA", "ssh-rsa " + base64.b64encode(b"x").decode(), line + "\n" + line):
            self.assertIsNone(U.validate_new_key(bad, lines)[0], bad)

    def test_fingerprint_matches_ssh_keygen_if_available(self):
        import shutil, subprocess, tempfile, os
        if not shutil.which("ssh-keygen"):
            self.skipTest("ssh-keygen not available")
        line = self.key_line()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k.pub")
            open(p, "w").write(line + "\n")
            out = subprocess.run(["ssh-keygen", "-lf", p], capture_output=True, text=True).stdout
        self.assertIn(U.parse_line(line).fingerprint, out)

    def test_failed_logins(self):
        t = ("Oct 22 sshd[1]: Failed password for root from 203.0.113.9 port 4000 ssh2\n"
             "Oct 22 sshd[1]: Failed password for invalid user a from 203.0.113.9 port 4001 ssh2\n"
             "Oct 22 sshd[1]: Invalid user x from 198.51.100.7 port 5\n"
             "root ssh:notty 192.0.2.1 Wed Oct 21 10:00\n")
        self.assertEqual(U.top_ips(t)[:2], [("203.0.113.9", 2), ("198.51.100.7", 1)])
        self.assertIn(("192.0.2.1", 1), U.top_ips(t))


if __name__ == "__main__":
    unittest.main()
