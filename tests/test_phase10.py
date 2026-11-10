import json
import unittest

from services import docker as D
from services import installplan as IP
from services import nginx as N

PF_UBUNTU = """##OS
ID=ubuntu
ID_LIKE=debian
VERSION_ID="24.04"
PRETTY_NAME="Ubuntu 24.04 LTS"
##ARCH
x86_64
##DISK
/dev/sda1 41152812 20576406 18000000 54% /
##MEM
MemTotal:        2005000 kB
##DNS
ok
##NET
301
##PORT80
##CONFLICTS
"""


def pf(**kw):
    d = IP.parse_preflight(PF_UBUNTU)
    d.update(kw)
    return d


class PreflightTests(unittest.TestCase):
    def test_parse(self):
        d = IP.parse_preflight(PF_UBUNTU)
        self.assertEqual((d["id"], d["like"], d["arch"], d["disk_kb"], d["mem_kb"]),
                         ("ubuntu", ["debian"], "x86_64", 18000000, 2005000))
        self.assertTrue(d["dns"] and d["net"] == "301" and not d["port80"] and d["conflicts"] == [])
        e = IP.parse_preflight("")
        self.assertEqual((e["disk_kb"], e["net"], e["dns"]), (0, "none", False))

    def test_docker_plan_ok(self):
        p = IP.build_plan("docker", pf(), "apt", "password", True, "ubuntu", {"hello": True, "group": True})
        self.assertFalse(p.blocked)
        names = [s.name for s in p.steps]
        self.assertEqual(names[0], "Refresh package index")
        self.assertIn("apt-get install -y docker.io", p.steps[1].cmd)
        self.assertIn("Enable and start docker", names)
        self.assertIn("Add ubuntu to the docker group", names)
        self.assertIn("Verify: run hello-world", names)
        self.assertTrue(all(s.sudo for s in p.steps if "Install" in s.name or "Enable" in s.name))
        self.assertTrue(p.warnings and p.est_disk and p.changes)
        plain = IP.build_plan("docker", pf(), "apt", "root", True, "root", {})
        self.assertNotIn("docker group", " ".join(s.name for s in plain.steps))     # opt-in only

    def test_docker_blockers(self):
        cases = [
            dict(pm="dnf", over=dict(id="rocky", like=["rhel"])),                  # not in default repos
            dict(pm="apt", sudo="none"), dict(pm=None),
            dict(pm="apt", over=dict(disk_kb=200_000)),
            dict(pm="apt", over=dict(conflicts=["podman-docker"])),
            dict(pm="apt", over=dict(dns=False, net="000")),
        ]
        for c in cases:
            p = IP.build_plan("docker", pf(**c.get("over", {})), c["pm"], c.get("sudo", "root"), True, "u")
            self.assertTrue(p.blocked, c)
            self.assertEqual(p.steps, [], c)                                     # no steps when blocked
        self.assertFalse(IP.build_plan("docker", pf(id="fedora", like=[]), "dnf", "root", True, "u").blocked)
        warn = IP.build_plan("docker", pf(mem_kb=300_000, arch="mips"), "apt", "root", True, "u")
        self.assertFalse(warn.blocked)
        self.assertEqual(sum(c.status == "warn" for c in warn.checks), 2)

    def test_nginx_plan(self):
        p = IP.build_plan("nginx", pf(), "apt", "passwordless", True, "u")
        self.assertFalse(p.blocked)
        self.assertEqual(p.packages, ["nginx"])
        self.assertIn("Verify: config test", [s.name for s in p.steps])
        w = IP.build_plan("nginx", pf(port80=True, conflicts=["apache2"]), "apt", "root", True, "u")
        self.assertFalse(w.blocked)
        self.assertTrue(any(c.status == "warn" and "Port 80" in c.name for c in w.checks))
        a = IP.build_plan("nginx", pf(id="alpine", like=[]), "apk", "root", False, "u")
        self.assertIn("rc-update add nginx", " ".join(s.cmd for s in a.steps))

    def test_user_validation_for_group(self):
        p = IP.build_plan("docker", pf(), "apt", "root", True, "bad user;rm", {"group": True})
        self.assertNotIn("usermod", " ".join(s.cmd for s in p.steps))


class DockerTests(unittest.TestCase):
    def test_detect(self):
        ok = D.parse_detect("##INSTALLED\nDocker version 24.0.7, build x\n##INFO\n24.0.7\n##COMPOSE\nDocker Compose version v2.21\n")
        self.assertEqual((ok.installed, ok.daemon, ok.server_version, ok.compose), (True, "running", "24.0.7", True))
        self.assertEqual(D.parse_detect("##INSTALLED\nDocker version 24\n##INFO\npermission denied while trying to connect\n##COMPOSE\n").daemon, "denied")
        self.assertEqual(D.parse_detect("##INSTALLED\nDocker version 24\n##INFO\nCannot connect to the Docker daemon at unix:///var/run/docker.sock\n##COMPOSE\n").daemon, "stopped")
        self.assertFalse(D.parse_detect("##MISSING\n").installed)

    def test_parsers(self):
        ps = "\n".join(json.dumps(x) for x in [
            {"ID": "a1", "Names": "web", "Image": "nginx", "State": "running", "Status": "Up 2h", "Ports": "0.0.0.0:80->80/tcp"},
            {"ID": "b2", "Names": "db", "Image": "pg", "State": "exited", "Status": "Exited (0) 1h ago"}])
        cs = D.parse_containers(ps + "\nnot json")
        self.assertEqual([c.name for c in cs], ["web", "db"])                 # running first
        im = D.parse_images(json.dumps({"ID": "x", "Repository": "nginx", "Tag": "latest", "Size": "1MB"}) + "\n" +
                            json.dumps({"ID": "y", "Repository": "<none>", "Tag": "<none>", "Size": "2MB"}))
        self.assertEqual(sorted(i.ref for i in im), ["nginx:latest", "y"])
        self.assertEqual(D.parse_stats(json.dumps({"Name": "web", "CPUPerc": "1%", "MemUsage": "5MiB / 1GiB"}))["web"]["cpu"], "1%")
        self.assertEqual(D.parse_networks(json.dumps({"ID": "1", "Name": "bridge", "Driver": "bridge", "Scope": "local"}))[0]["name"], "bridge")
        cp = D.parse_compose_ls(json.dumps([{"Name": "app", "Status": "running(2)", "ConfigFiles": "/srv/app/compose.yml,/srv/app/o.yml"}]))
        self.assertEqual((cp[0].name, len(cp[0].files)), ("app", 2))

    def test_commands_are_validated_and_quoted(self):
        self.assertEqual(D.container_command("restart", "web-1"), "docker restart web-1")
        self.assertEqual(D.container_command("rmf", "web"), "docker rm -f web")
        for bad in ("a b", "x;rm -rf /", "$(id)", "-rf", ""):
            with self.assertRaises(ValueError):
                D.container_command("stop", bad)
        with self.assertRaises(ValueError):
            D.container_command("exec", "x")
        self.assertEqual(D.pull_command("ghcr.io/org/app:1.2"), "docker pull ghcr.io/org/app:1.2")
        with self.assertRaises(ValueError):
            D.pull_command("img; reboot")
        self.assertIn("-f", D.logs_command("web", follow=True))
        self.assertIn("--tail 5000", D.logs_command("web", tail=10**6))
        p = D.ComposeProject("app", "x", ["/srv/my app/compose.yml"])
        self.assertEqual(D.compose_command(p, "up"), "docker compose -f '/srv/my app/compose.yml' -p app up -d")
        with self.assertRaises(ValueError):
            D.compose_command(D.ComposeProject("app", "x", ["relative.yml"]), "up")
        self.assertIn("exec bash", D.exec_command("web"))
        self.assertTrue(all(c.startswith("docker ") for c in D.PRUNES.values()))


class NginxTests(unittest.TestCase):
    def test_detect_and_sites(self):
        s = N.parse_detect("##INSTALLED\nnginx version: nginx/1.24.0 (Ubuntu)\n##ACTIVE\nactive\n##CERTBOT\nyes\n")
        self.assertEqual((s.installed, s.version, s.active, s.certbot), (True, "1.24.0", "active", True))
        self.assertFalse(N.parse_detect("##MISSING").installed)
        sites, layout = N.parse_sites("##SITES\ndefault\nexample.com\n##ENABLED\nexample.com\n")
        self.assertEqual(layout, "sites")
        self.assertEqual([(x.name, x.enabled) for x in sites], [("default", False), ("example.com", True)])
        sites, layout = N.parse_sites("##CONFD\na.conf\nb.conf.disabled\nreadme.txt\n")
        self.assertEqual((layout, [(x.name, x.enabled, x.path) for x in sites]),
                         ("confd", [("a.conf", True, "/etc/nginx/conf.d/a.conf"),
                                    ("b.conf", False, "/etc/nginx/conf.d/b.conf.disabled")]))

    def test_toggle_and_paths(self):
        site = N.Site("example.com", False, "sites", "/etc/nginx/sites-available/example.com")
        do, undo = N.toggle_commands(site, True)
        self.assertEqual(do, "ln -s /etc/nginx/sites-available/example.com /etc/nginx/sites-enabled/example.com")
        self.assertTrue(undo.startswith("rm -f /etc/nginx/sites-enabled/"))
        c = N.Site("a.conf", False, "confd", "x")
        self.assertEqual(N.toggle_commands(c, True)[0], "mv /etc/nginx/conf.d/a.conf.disabled /etc/nginx/conf.d/a.conf")
        with self.assertRaises(ValueError):
            N.toggle_commands(N.Site("../../etc/passwd", True, "sites", "x"), False)
        self.assertEqual(N.site_path("confd", "api"), "/etc/nginx/conf.d/api.conf")
        self.assertTrue(N.reload_command().find("nginx -t &&") > 0)
        self.assertIn("nginx -s reload", N.reload_command(False))

    def test_templates(self):
        self.assertIn("proxy_pass http://127.0.0.1:3000;", N.render_template("proxy", "app.example.com", "http://127.0.0.1:3000"))
        t = N.render_template("static", "example.com", "/var/www/site")
        self.assertIn("root /var/www/site;", t)
        self.assertEqual(t.count("{"), t.count("}"))
        self.assertIn("fastcgi_pass unix:", N.render_template("php", "example.com", "/var/www/p"))
        for kind, name, target in [("static", "a b", "/x"), ("static", "x;y", "/x"), ("static", "ok.com", "rel/path"),
                                   ("static", "ok.com", "/a b"), ("proxy", "ok.com", "javascript:x"),
                                   ("proxy", "ok.com", "http://x;rm"), ("bogus", "ok.com", "/x")]:
            with self.assertRaises(ValueError, msg=(kind, name, target)):
                N.render_template(kind, name, target)

    def test_certbot(self):
        st = N.certbot_steps("apt", False, ["example.com", "www.example.com"], "me@example.com")
        self.assertEqual(len(st), 3)
        self.assertIn("-d example.com -d www.example.com", st[-1].cmd)
        self.assertEqual(len(N.certbot_steps("apt", True, ["a.io"], "a@b.io")), 1)
        for dom, mail in [(["bad_domain"], "a@b.io"), (["a.io; reboot"], "a@b.io"), ([], "a@b.io"), (["a.io"], "nope")]:
            with self.assertRaises(ValueError):
                N.certbot_steps("apt", True, dom, mail)


if __name__ == "__main__":
    unittest.main()
