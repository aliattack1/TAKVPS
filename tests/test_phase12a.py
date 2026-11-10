import asyncio
import tempfile
import unittest
from pathlib import Path

from core.tunnels import TunnelManager
from services import dbs as D
from services import diskusage as DU
from services import envfile as E
from services import health as H
from services import tunnels as T


class HealthTests(unittest.TestCase):
    def test_perfect_and_penalties(self):
        s, issues = H.compute(H.HealthInputs())
        self.assertEqual((s, issues), (100, []))
        s, issues = H.compute(H.HealthInputs(disk_pct=96, disk_mount="/var", mem_pct=91, load_per_core=1.2,
                                             failed=["a.service", "b.service"], security=3, updates=25, reboot=True))
        self.assertEqual(sum(i.penalty for i in issues), 25 + 9 + 8 + 16 + 15 + 8 + 6)
        self.assertEqual(s, 100 - 87)
        self.assertEqual(issues[0].key, "disk")                                  # biggest first
        self.assertEqual({i.key: i.fix for i in issues}["failed"], "services")
        self.assertEqual({i.key: i.fix for i in issues}["disk"], "diskusage")
        self.assertEqual(H.compute(H.HealthInputs(failed=[f"s{i}.service" for i in range(9)]))[1][0].penalty, 24)   # capped
        self.assertEqual(H.compute(H.HealthInputs(disk_pct=99, mem_pct=99, load_per_core=5, failed=["x.service"] * 9,
                                                  security=9, updates=99, reboot=True, swap_pct=99))[0], 0)         # floor at 0
        self.assertEqual([H.rating(x) for x in (95, 80, 60, 10)], ["Excellent", "Good", "Needs attention", "Critical"])

    def test_parse(self):
        h = H.parse_health("##LOAD\n2.00 1 1 1/1 1\n##CORES\n4\n##MEM\nMemTotal: 1000 kB\nMemAvailable: 250 kB\nSwapTotal: 100 kB\nSwapFree: 10 kB\n"
                           "##DISK\n/dev/sda1 100 50 50 50% /\n/dev/sdb1 100 92 8 92% /data\n##FAILED\nfoo.service\nnot-a-unit\n##REBOOT\nyes\n")
        self.assertEqual((h.disk_pct, h.disk_mount, h.failed, h.reboot), (92, "/data", ["foo.service"], True))
        self.assertAlmostEqual(h.load_per_core, 0.5)
        self.assertAlmostEqual(h.mem_pct, 75.0)
        self.assertAlmostEqual(h.swap_pct, 90.0)
        self.assertEqual(H.parse_health("").disk_pct, 0)


class EnvTests(unittest.TestCase):
    TEXT = ("# app config\nDEBUG=true\nexport PORT=8080   # inline\nDB_PASSWORD=\"p@ss w0rd\"\nSECRET='a#b'\nEMPTY=\n\nbad line\n#COMMENTED=1\n")

    def test_parse_roundtrip_and_values(self):
        ls = E.parse_env(self.TEXT)
        vars_ = {l.key: l.value for l in ls if l.kind == "var"}
        self.assertEqual(vars_, {"DEBUG": "true", "PORT": "8080", "DB_PASSWORD": "p@ss w0rd", "SECRET": "a#b", "EMPTY": ""})
        self.assertTrue(next(l for l in ls if l.key == "PORT").export)
        self.assertEqual(E.serialize(ls), self.TEXT)                              # nothing changed => identical text
        self.assertFalse(any(l.key == "COMMENTED" for l in ls))

    def test_edit_preserves_everything_else(self):
        ls = E.set_var(E.parse_env(self.TEXT), "PORT", "9090")
        out = E.serialize(ls)
        self.assertIn("export PORT=9090\n", out)
        self.assertIn("# app config\n", out)
        self.assertIn('DB_PASSWORD="p@ss w0rd"\n', out)
        out = E.serialize(E.set_var(E.parse_env(self.TEXT), "NEW_KEY", "a b $x"))
        self.assertTrue(out.endswith("NEW_KEY='a b $x'\n"))
        out = E.serialize(E.remove_var(E.parse_env(self.TEXT), "DEBUG"))
        self.assertNotIn("DEBUG", out)
        for bad in ("1BAD", "a b", "a-b", ""):
            with self.assertRaises(ValueError):
                E.set_var([], bad, "x")

    def test_format_and_secret(self):
        self.assertEqual(E.format_value("plain-1.2"), "plain-1.2")
        self.assertEqual(E.format_value("it's"), '"it\'s"')
        self.assertEqual(E.format_value('a"b $c'), "'a\"b $c'")
        self.assertEqual(E.format_value("it's $x"), '"it\'s \\$x"')
        rt = E.parse_env(E.serialize(E.set_var([], "K", "it's \"quoted\" $v\nline2")))[0].value
        self.assertEqual(rt, "it's \"quoted\" $v\nline2")                         # round-trips through our own format
        self.assertTrue(all(E.is_secret(k) for k in ("DB_PASSWORD", "api_key", "JWT_SECRET", "AWS_ACCESS_KEY_ID", "AUTH_TOKEN")))
        self.assertFalse(E.is_secret("PORT") or E.is_secret("DEBUG"))
        self.assertEqual(E.mask("hunter2"), "•••••••")
        self.assertEqual(E.mask(""), "(empty)")
        self.assertTrue(E.looks_like_env(".env") and E.looks_like_env(".env.production") and not E.looks_like_env("env.py"))


class DbTests(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(D.detect("mariadb\npsql\nredis-cli\nsqlite3\n"), ["mysql", "postgres", "sqlite", "redis"])
        self.assertEqual(D.detect(""), [])

    def test_password_never_in_command(self):
        a = D.Auth(mode="password", user="app", password="s3cr3t!'\"", host="127.0.0.1", port=3306)
        for engine, db in (("mysql", "shop"), ("postgres", "shop")):
            c = D.build(engine, a, "SELECT 1", db)
            self.assertNotIn("s3cr3t", c.cmd)
            self.assertEqual(c.stdin, a.password + "\n")
            self.assertFalse(c.privileged)
            self.assertIn("read -r P", c.cmd)
        for c in (D.dump_command("mysql", a, "shop", "/bk/x.sql.gz"), D.restore_command("postgres", a, "shop", "/bk/x.sql.gz"),
                  D.build("redis", D.Auth(mode="password", password="pw!", host="127.0.0.1"), redis_args=["GET", "k"])):
            self.assertNotIn("s3cr3t", c.cmd)
            self.assertNotIn("pw!", c.cmd)
        self.assertIsNone(D.build("mysql", D.Auth(), "SELECT 1").stdin)
        self.assertTrue(D.build("mysql", D.Auth(), "SELECT 1").privileged)
        self.assertIn("runuser -u postgres", D.build("postgres", D.Auth(), "SELECT 1").cmd)

    def test_injection_is_blocked(self):
        for bad in ("db; rm -rf /", "a b", "x`id`", "d$(id)"):
            with self.assertRaises(ValueError, msg=bad):
                D.build("mysql", D.Auth(), "SELECT 1", bad)
            with self.assertRaises(ValueError, msg=bad):
                D.sql_tables("mysql", bad)
        with self.assertRaises(ValueError):
            D.sql_tables("mysql", "")
        with self.assertRaises(ValueError):
            D.sql_rows("postgres", "x", "t; DROP TABLE x", 0)
        with self.assertRaises(ValueError):
            D.build("mysql", D.Auth(mode="password", user="u", host="h; id"), "SELECT 1")
        with self.assertRaises(ValueError):
            D.dump_command("mysql", D.Auth(), "db", "relative.sql.gz")
        with self.assertRaises(ValueError):
            D.dump_command("mysql", D.Auth(), "db", "/bk/x.sql")
        c = D.build("mysql", D.Auth(), "SELECT 'it''s'; -- x", "shop")
        self.assertIn("-e 'SELECT", c.cmd)                                            # the SQL itself is quoted
        self.assertEqual(D.sql_rows("mysql", "shop", "orders", 2), "SELECT * FROM `shop`.`orders` LIMIT 50 OFFSET 100")
        self.assertIn('"public"."users"', D.sql_rows("postgres", "x", "public.users", 0))
        self.assertIn("umask 077", D.dump_command("postgres", D.Auth(), "db", "/bk/x.sql.gz").cmd)

    def test_sql_classification(self):
        K = D.sql_kind
        for s in ("SELECT * FROM t", "show tables", "EXPLAIN SELECT 1", "PRAGMA table_info(t)", "WITH x AS (SELECT 1) SELECT * FROM x",
                  "SELECT 'a;DROP TABLE x' AS s"):
            self.assertEqual(K(s), "read", s)
        for s in ("INSERT INTO t VALUES (1)", "UPDATE t SET a=1 WHERE id=2", "CREATE TABLE x(a int)", "PRAGMA journal_mode=WAL",
                  "WITH x AS (SELECT 1) DELETE FROM t WHERE id IN (SELECT * FROM x)"):
            self.assertIn(K(s), ("write", "destructive"), s)
        for s in ("DROP TABLE t", "TRUNCATE t", "DELETE FROM t", "UPDATE t SET a=1", "SELECT 1; DROP DATABASE x", "ALTER TABLE t ADD c int"):
            self.assertEqual(K(s), "destructive", s)
        for s in ("SELECT * INTO OUTFILE '/tmp/x' FROM t", "SELECT LOAD_FILE('/etc/passwd')", "COPY t TO PROGRAM 'id'",
                  "\\! id", "SELECT pg_read_file('/etc/passwd')", "LOAD DATA INFILE 'x' INTO TABLE t"):
            self.assertEqual(K(s), "blocked", s)
        self.assertEqual(D.split_statements("a; b 'x;y'; -- c\n d"), ["a", "b 'x;y'", "d"])
        self.assertEqual(D.redis_kind(["get", "k"]), "read")
        self.assertEqual(D.redis_kind(["SET", "k", "v"]), "write")
        self.assertEqual(D.redis_kind(["FLUSHALL"]), "destructive")
        for c in ("CONFIG", "eval", "KEYS", "shutdown", "MONITOR"):
            self.assertEqual(D.redis_kind([c]), "blocked", c)
        self.assertEqual(D.redis_inspect_args("k", "hash"), ["HGETALL", "k"])

    def test_results(self):
        r = D.parse_result("mysql", "id\tname\n1\ta\\tb\n2\tNULL\n")
        self.assertEqual((r.columns, r.rows), (["id", "name"], [["1", "a\tb"], ["2", "NULL"]]))
        r = D.parse_result("postgres", 'id,name\n1,"a,b"\n2,\n')
        self.assertEqual((r.columns, r.rows), (["id", "name"], [["1", "a,b"], ["2", ""]]))
        self.assertTrue(D.parse_result("postgres", "ERROR:  relation \"x\" does not exist").error)
        self.assertTrue(D.parse_result("mysql", "ERROR 1045 (28000): Access denied").error)
        self.assertTrue(D.parse_result("mysql", "sh: 1: mysql: command not found").error)
        self.assertEqual(D.parse_result("postgres", "UPDATE 3").rows, [])
        self.assertEqual(D.parse_result("sqlite", "").text, "(no output)")


class FakeListener:
    def __init__(self, port):
        self.port, self.closed = port, False

    def get_port(self):
        return self.port

    def close(self):
        self.closed = True


class FakeSSH:
    def __init__(self):
        self.calls = []

    async def forward(self, local, host, port):
        self.calls.append((local, host, port))
        return FakeListener(local or 43210)


class TunnelTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_and_favorites(self):
        self.assertIsNone(T.validate(T.TunnelSpec("db", 5432)))
        for spec in (T.TunnelSpec("x", 0), T.TunnelSpec("x", 70000), T.TunnelSpec("x", 80, "bad host"), T.TunnelSpec("x", 80, "-oProxy"),
                     T.TunnelSpec("x", 80, local_port=80), T.TunnelSpec("x", 80, local_port=70000)):
            self.assertIsNotNone(T.validate(spec), spec)
        with tempfile.TemporaryDirectory() as d:
            f = T.FavoritesStore(Path(d) / "t.json")
            s = T.TunnelSpec("db", 5432, "10.0.0.5", 15432)
            f.add("srv1", s)
            f.add("srv1", T.TunnelSpec("web", 8080))
            self.assertEqual([x.name for x in T.FavoritesStore(Path(d) / "t.json").list("srv1")], ["db", "web"])
            self.assertEqual(f.list("srv2"), [])
            f.remove("srv1", s.id)
            self.assertEqual([x.name for x in f.list("srv1")], ["web"])

    async def test_manager(self):
        ssh = FakeSSH()
        m = TunnelManager(ssh)
        t = await m.start(T.TunnelSpec("web", 8080))
        self.assertEqual((t.port, t.local_url), (43210, "http://127.0.0.1:43210"))
        self.assertEqual(ssh.calls, [(0, "127.0.0.1", 8080)])
        self.assertIs(await m.start(t.spec), t)                       # starting twice is a no-op
        self.assertEqual(len(ssh.calls), 1)
        with self.assertRaises(ValueError):
            await m.start(T.TunnelSpec("bad", 0))
        m.stop(t.spec.id)
        self.assertTrue(t.listener.closed and not m.active)
        a = await m.start(T.TunnelSpec("a", 81))
        b = await m.start(T.TunnelSpec("b", 82))
        m.stop_all()
        self.assertTrue(a.listener.closed and b.listener.closed and not m.active)


class DiskTests(unittest.TestCase):
    def test_du(self):
        total, es = DU.parse_du("1000\t/var\n600\t/var/lib\n300\t/var/log\n100\t/var/cache\nbad line\n", "/var/")
        self.assertEqual((total, [e.name for e in es], es[0].kb), (1000, ["lib", "log", "cache"], 600))
        self.assertEqual(DU.parent("/var/log"), "/var")
        self.assertEqual(DU.parent("/"), "/")
        self.assertIn("du -xk --max-depth=1 '/my dir'", DU.du_command("/my dir"))
        with self.assertRaises(ValueError):
            DU.du_command("relative")

    def test_sizes_and_catalogue(self):
        self.assertEqual(DU.parse_size_text("1.5GB (50%)"), int(1.5 * 1024 ** 2))
        self.assertEqual(DU.parse_size_text("350 MiB"), 350 * 1024)
        self.assertEqual(DU.parse_size_text("Archived and active journals take up 1.2G in the file system."), int(1.2 * 1024 ** 2))
        self.assertIsNone(DU.parse_size_text("none"))
        apt = DU.items_for("apt", True, True)
        self.assertEqual([i.key for i in apt], ["pkgcache", "autoremove", "journal", "oldlogs", "dimages", "dvolumes", "dbuild"])
        self.assertEqual([i.key for i in DU.items_for("dnf", False, False)], ["pkgcache", "oldlogs"])
        self.assertTrue(next(i for i in apt if i.key == "dvolumes").caution)
        self.assertFalse(next(i for i in apt if i.key == "pkgcache").caution)
        script = DU.measure_script(apt)
        for it in apt:
            self.assertIn(f"##{it.key}", script)
        out = DU.parse_measure("##pkgcache\n123456\n##autoremove\n4\n##journal\nArchived and active journals take up 200.0M in the file system.\n"
                               "##oldlogs\n\n##dimages\nImages|1.2GB (50%)\n", apt)
        self.assertEqual((out["pkgcache"], out["autoremove"], out["oldlogs"], out["dimages"]), (123456, 4, None, int(1.2 * 1024 ** 2)))
        self.assertEqual(out["journal"], 200 * 1024)
        self.assertIsNone(out["dvolumes"])


if __name__ == "__main__":
    unittest.main()
