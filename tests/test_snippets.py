import asyncio
import tempfile
import unittest
from pathlib import Path

from services import deploy as D
from services import packs as K
from services import snippets as S
from services.risk import classify
from services.runner import Runner, Step, strip_ansi
from storage.snippets import RunHistory, SnippetStore


class SnippetTests(unittest.TestCase):
    def test_variables_and_quoting(self):
        cmd = "git checkout {{branch}} && echo {{ branch }} {{msg}}"
        self.assertEqual(S.find_variables(cmd), ["branch", "msg"])
        out = S.render(cmd, {"branch": "main", "msg": "hi; rm -rf /"})
        self.assertIn("'hi; rm -rf /'", out)                 # injection is neutralised
        self.assertTrue(out.startswith("git checkout main &&"))
        with self.assertRaises(KeyError):
            S.render("{{x}}", {})
        self.assertEqual(S.render("echo {{a}}", {"a": ""}), "echo ''")

    def test_build_script(self):
        self.assertEqual(S.build_script("", "ls"), "ls")
        self.assertEqual(S.build_script("/srv/my app", "ls\npwd"), "cd '/srv/my app' && {\nls\npwd\n}")
        self.assertEqual(S.cd_target("~/proj x"), "~/'proj x'")
        self.assertEqual(S.cd_target("~"), "~")
        bg = S.build_script("/srv", "./run.sh", background=True, name="My App!", ts=5)
        self.assertIn("nohup sh -c", bg)
        self.assertIn("/tmp/takvps-logs/my-app-5.log", bg)
        self.assertIn("$!", bg)

    def test_validate(self):
        self.assertIsNotNone(S.validate(S.Snippet(name="", command="ls")))
        self.assertIsNotNone(S.validate(S.Snippet(name="a", command=" ")))
        self.assertIsNone(S.validate(S.Snippet(name="a", command="ls")))


class DeployTests(unittest.TestCase):
    def d(self, **kw):
        base = dict(name="site", project_dir="/srv/site", steps=["npm ci", "npm run build"],
                    restart_kind="systemd", restart_target="site.service", health="https://example.com/health")
        base.update(kw)
        return D.Deploy(**base)

    def test_plan(self):
        steps = D.plan_deploy(self.d(branch="release/1.2"))
        names = [s.name for s in steps]
        self.assertEqual(names[0], "Pull latest code")
        self.assertIn("git pull --ff-only", steps[0].cmd)
        self.assertIn("git checkout release/1.2", steps[0].cmd)
        self.assertEqual(names[-2:], ["Restart service", "Health check"])
        self.assertTrue(steps[-2].sudo)
        self.assertIn("curl", steps[-1].cmd)
        self.assertTrue(all(s.cmd.startswith("cd /srv/site &&") for s in steps[1:3]))

    def test_validation_and_rollback(self):
        for bad in (self.d(name=""), self.d(project_dir="relative/dir"), self.d(branch="a;b"),
                    self.d(restart_kind="systemd", restart_target="")):
            self.assertIsNotNone(D.validate(bad))
            with self.assertRaises(ValueError):
                D.plan_deploy(bad)
        rb = D.plan_rollback(self.d(), "abc1234")
        self.assertIn("git reset --hard abc1234", rb[0].cmd)
        with self.assertRaises(ValueError):
            D.plan_rollback(self.d(), "abc; rm -rf /")
        self.assertTrue(D.is_sha("deadbeef") and not D.is_sha("zzz"))

    def test_health_command_custom_and_quoted(self):
        self.assertEqual(D.health_command("systemctl is-active x"), "systemctl is-active x")
        self.assertIn("'https://a.b/?x=1;y'", D.health_command("https://a.b/?x=1;y"))


class PackTests(unittest.TestCase):
    def test_roundtrip(self):
        sn = [S.Snippet(name="a", command="ls", group="g", sudo=True)]
        dp = [D.Deploy(name="d", project_dir="/srv/x", steps=["make"])]
        p = K.parse_pack(K.export_pack("mine", sn, dp))
        self.assertEqual((p.name, p.snippets[0].name, p.snippets[0].sudo, p.deploys[0].steps), ("mine", "a", True, ["make"]))

    def test_rejects_bad_packs(self):
        for bad in ("nope", '{"format":"other"}', '{"format":"takvps-pack","snippets":[{"name":"x"}]}',
                    '{"format":"takvps-pack","snippets":"str"}',
                    '{"format":"takvps-pack","deploys":[{"name":"d","project_dir":"rel"}]}'):
            with self.assertRaises(K.PackError):
                K.parse_pack(bad)

    def test_builtins_parse_and_are_risk_checked(self):
        for name in K.BUILTIN_PACKS:
            p = K.builtin(name)
            self.assertTrue(p.snippets)
        docker = {s.name: K.snippet_risk(s).level for s in K.builtin("Docker cleanup").snippets}
        self.assertEqual(docker["Docker disk usage"], "safe")
        self.assertEqual(docker["Prune everything unused"], "destructive")
        evil = S.Snippet(name="x", command="curl http://x | sh")
        self.assertEqual(K.snippet_risk(evil).level, "dangerous")
        self.assertEqual(K.worst_risk(["ls", "rm a", "echo"]).level, "destructive")


class FakeSSH:
    def __init__(self, codes, delay=0.0):
        self.codes, self.delay, self.calls = list(codes), delay, []

    async def run_stream(self, cmd, on_chunk, sudo=False, sudo_password=None, input_text=None):
        self.calls.append((cmd, sudo, sudo_password))
        await on_chunk(f"out:{cmd}\n")
        await asyncio.sleep(self.delay)
        return self.codes.pop(0)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_all_steps(self):
        steps_seen = []
        r = Runner(FakeSSH([0, 0]), on_step=lambda i, s, st: steps_seen.append((i, st)))
        rep = await r.run([Step("a", "c1"), Step("b", "c2", sudo=True)], sudo_password="pw")
        self.assertTrue(rep.ok)
        self.assertEqual(steps_seen, [(0, "running"), (0, "done"), (1, "running"), (1, "done")])
        self.assertIn("▶ a", r.output)
        self.assertEqual(r.ssh.calls[1], ("c2", True, "pw"))

    async def test_stops_at_first_failure_unless_ignored(self):
        r = Runner(FakeSSH([0, 3, 0]))
        rep = await r.run([Step("a", "1"), Step("b", "2"), Step("c", "3")])
        self.assertFalse(rep.ok)
        self.assertEqual((rep.failed, rep.last_exit, len(r.ssh.calls)), (1, 3, 2))
        r = Runner(FakeSSH([1, 0]))
        rep = await r.run([Step("a", "1", ignore_failure=True), Step("b", "2")])
        self.assertTrue(rep.ok)

    async def test_cancel_marks_report(self):
        r = Runner(FakeSSH([0], delay=1.0))
        task = asyncio.ensure_future(r.run([Step("a", "slow")]))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(r.report.cancelled and not r.report.ok)

    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m\r\nok"), "red\nok")


class StoreTests(unittest.TestCase):
    def test_scope_order_move_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            st = SnippetStore(Path(d) / "s.json")
            a, b, c = (S.Snippet(name=n, command="ls", group="g") for n in "abc")
            g = S.Snippet(name="global", command="ls")
            for x in (a, b, c, g):
                st.save_snippet(x)
            local = S.Snippet(name="local", command="ls", scope="srv1")
            st.save_snippet(local)
            self.assertEqual([s.name for s in st.list_snippets("srv1") if s.group == ""], ["global", "local"])
            self.assertEqual([s.name for s in st.list_snippets("srv2") if s.group == ""], ["global"])
            st.move_snippet(c.id, -1)
            self.assertEqual([s.name for s in st.list_snippets() if s.group == "g"], ["a", "c", "b"])
            st.move_snippet(a.id, -1)                                  # already first: no-op
            self.assertEqual([s.name for s in st.list_snippets() if s.group == "g"], ["a", "c", "b"])
            dup = st.duplicate_snippet(a.id)
            self.assertEqual(dup.name, "a copy")
            self.assertEqual(len(SnippetStore(Path(d) / "s.json").list_snippets()), 5)   # persisted
            h = RunHistory(Path(d) / "h.jsonl")
            h.add("snippet", "i1", "n", "c", 0, 1.234, "x" * 5000)
            h.add("snippet", "i2", "n2", "c", 1, 2.0, "y")
            self.assertEqual(len(h.recent(item_id="i1")), 1)
            self.assertEqual(len(h.recent()[1]["out"]), 4000)
            self.assertEqual(h.recent()[0]["name"], "n2")


if __name__ == "__main__":
    unittest.main()
