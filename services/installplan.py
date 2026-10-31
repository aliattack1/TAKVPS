"""Preflight checks + install plans for Docker and Nginx. Pure: parsing and planning only.

Nothing is installed from here; the UI shows the plan, asks for confirmation, then runs the steps.
Packages come from the distro's own repositories via the native package manager (no `curl | sh`,
no third-party repositories are added).
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from services import packages as P
from services.runner import Step

_SCRIPT = r"""
echo '##OS'; grep -E '^(ID|ID_LIKE|VERSION_ID|PRETTY_NAME)=' /etc/os-release 2>/dev/null
echo '##ARCH'; uname -m
echo '##DISK'; df -Pk / 2>/dev/null | tail -n 1
echo '##MEM'; grep MemTotal /proc/meminfo 2>/dev/null
echo '##DNS'; getent hosts example.com >/dev/null 2>&1 && echo ok || echo fail
echo '##NET'; if command -v curl >/dev/null 2>&1; then curl -s -o /dev/null -w '%{http_code}' --connect-timeout 6 --max-time 10 https://1.1.1.1 2>/dev/null; echo; else echo none; fi
echo '##PORT80'; ss -ltn 2>/dev/null | grep -E ':80[[:space:]]' | head -n 1
echo '##CONFLICTS'; for p in podman-docker docker-engine apache2 httpd lighttpd caddy; do (dpkg -s $p || rpm -q $p || pacman -Qi $p || apk info -e $p) >/dev/null 2>&1 && echo $p; done; true
"""
PREFLIGHT_CMD = "sh -c " + shlex.quote(_SCRIPT)

KNOWN_ARCH = {"x86_64", "amd64", "aarch64", "arm64", "armv7l", "armv6l", "ppc64le", "s390x"}
DEBIAN_LIKE = {"debian", "ubuntu", "raspbian", "linuxmint", "pop", "kali", "elementary", "zorin"}
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@dataclass
class Check:
    name: str
    status: str            # ok | warn | fail
    detail: str = ""


@dataclass
class Plan:
    kind: str
    title: str
    checks: list[Check] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    est_disk: str = ""
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:].strip()
            out[cur] = []
        elif cur is not None and ln.strip():
            out[cur].append(ln.rstrip())
    return out


def parse_preflight(text: str) -> dict:
    s = _sections(text)
    osr: dict[str, str] = {}
    for ln in s.get("OS", []):
        k, _, v = ln.partition("=")
        osr[k] = v.strip().strip('"')
    disk_kb = mem_kb = 0
    try:
        disk_kb = int(s.get("DISK", [""])[0].split()[3])          # "Available" column of df -Pk
    except (IndexError, ValueError):
        pass
    try:
        mem_kb = int(s.get("MEM", [""])[0].split()[1])
    except (IndexError, ValueError):
        pass
    return {"id": osr.get("ID", "").lower(), "like": osr.get("ID_LIKE", "").lower().split(),
            "version": osr.get("VERSION_ID", ""), "pretty": osr.get("PRETTY_NAME", ""),
            "arch": (s.get("ARCH") or [""])[0].strip(), "disk_kb": disk_kb, "mem_kb": mem_kb,
            "dns": (s.get("DNS") or ["fail"])[0].strip() == "ok",
            "net": (s.get("NET") or ["none"])[0].strip(), "port80": bool(s.get("PORT80")),
            "conflicts": [x.strip() for x in s.get("CONFLICTS", [])]}


def _mb(kb: int) -> str:
    return f"{kb / 1024 / 1024:.1f} GB" if kb >= 1024 * 1024 else f"{kb // 1024} MB"


def _common_checks(kind: str, pf: dict, pm: Optional[str], sudo: str, has_systemd: bool) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check("Root / sudo access",
                        "ok" if sudo in ("root", "passwordless", "password") else "fail",
                        {"root": "Logged in as root", "passwordless": "Passwordless sudo",
                         "password": "sudo (you'll be asked for your password)"}.get(sudo, "This user has no sudo access")))
    checks.append(Check("Package manager", "ok" if pm else "fail", pm or "No supported package manager detected"))
    arch = pf.get("arch", "")
    checks.append(Check("Architecture", "ok" if arch in KNOWN_ARCH else "warn", arch or "unknown"))
    need_fail, need_warn = (1_000_000, 3_000_000) if kind == "docker" else (100_000, 300_000)
    d = pf.get("disk_kb", 0)
    checks.append(Check("Free disk space (/)", "fail" if d < need_fail else "warn" if d < need_warn else "ok",
                        _mb(d) + " free" + ("" if d >= need_warn else f" (recommended ≥ {_mb(need_warn)})")))
    if kind == "docker":
        m = pf.get("mem_kb", 0)
        checks.append(Check("Memory", "warn" if m < 524288 else "ok", _mb(m) + " RAM"
                            + (" — Docker will be tight on this much memory" if m < 524288 else "")))
    net, dns = pf.get("net", "none"), pf.get("dns", False)
    reach = net[:1] in ("2", "3")
    checks.append(Check("Internet access", "ok" if reach else "warn" if (dns or net == "none") else "fail",
                        "Reachable" if reach else "Could not confirm HTTPS access" if dns or net == "none"
                        else "No DNS or HTTPS access; packages can't be downloaded"))
    checks.append(Check("init system", "ok" if has_systemd else "warn",
                        "systemd" if has_systemd else "No systemd detected; the service is enabled with the distro's init system if possible"))
    return checks


def build_plan(kind: str, pf: dict, pm: Optional[str], sudo: str, has_systemd: bool,
               user: str = "", opts: Optional[dict] = None) -> Plan:
    opts = opts or {}
    plan = Plan(kind, "Install Docker" if kind == "docker" else "Install Nginx")
    plan.checks = _common_checks(kind, pf, pm, sudo, has_systemd)
    distro = pf.get("id", "")
    like = set(pf.get("like", [])) | {distro}
    pretty = pf.get("pretty") or distro or "this system"

    # ---- distro support + packages ----
    pkgs: list[str] = []
    extra: list[Step] = []
    if pm:
        if kind == "docker":
            supported = {"apt": bool(like & DEBIAN_LIKE), "dnf": distro == "fedora", "yum": False,
                         "pacman": True, "apk": True, "zypper": True}[pm]
            plan.checks.insert(0, Check("Distribution supported", "ok" if supported else "fail",
                                        pretty if supported else
                                        f"{pretty}: Docker isn't in the default repositories. Add Docker's official "
                                        "repository manually; this app won't add third-party repositories."))
            pkgs = {"apt": ["docker.io"], "dnf": ["moby-engine"], "yum": ["docker"], "pacman": ["docker"],
                    "apk": ["docker"], "zypper": ["docker"]}[pm]
            compose = {"apt": "DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 || "
                              "DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose",
                       "dnf": P.cmd_install("dnf", ["docker-compose"]), "pacman": P.cmd_install("pacman", ["docker-compose"]),
                       "apk": P.cmd_install("apk", ["docker-cli-compose"]), "zypper": P.cmd_install("zypper", ["docker-compose"]),
                       "yum": ""}[pm]
            if compose:
                extra.append(Step("Install docker compose (optional)", compose, sudo=True, ignore_failure=True))
        else:
            plan.checks.insert(0, Check("Distribution supported", "ok", pretty))
            pkgs = ["nginx"]
    plan.packages = pkgs

    # ---- conflicts ----
    conflicts = pf.get("conflicts", [])
    if kind == "docker" and any(c in ("podman-docker", "docker-engine") for c in conflicts):
        bad = [c for c in conflicts if c in ("podman-docker", "docker-engine")]
        plan.checks.append(Check("Conflicting packages", "fail",
                                 f"{', '.join(bad)} is installed and conflicts with Docker. Remove it first."))
    elif kind == "nginx" and (pf.get("port80") or any(c in ("apache2", "httpd", "lighttpd", "caddy") for c in conflicts)):
        what = [c for c in conflicts if c in ("apache2", "httpd", "lighttpd", "caddy")]
        plan.checks.append(Check("Port 80 / other web servers", "warn",
                                 ("Port 80 is already in use. " if pf.get("port80") else "")
                                 + (f"Also installed: {', '.join(what)}. " if what else "")
                                 + "Nginx may fail to start until that is stopped or reconfigured."))
    else:
        plan.checks.append(Check("Conflicting software", "ok", "None found"))

    if not pm or plan.blocked:
        return plan

    # ---- steps ----
    steps: list[Step] = [Step("Refresh package index", P.cmd_refresh(pm), sudo=True),
                         Step(f"Install {', '.join(pkgs)}", P.cmd_install(pm, pkgs), sudo=True)] + extra
    svc = "docker" if kind == "docker" else "nginx"
    if has_systemd:
        steps.append(Step(f"Enable and start {svc}", f"systemctl enable --now {svc}", sudo=True))
    elif pm == "apk":
        steps.append(Step(f"Enable and start {svc}", f"rc-update add {svc} default && service {svc} start", sudo=True))
    if kind == "docker":
        if opts.get("group") and _USER.match(user):
            steps.append(Step(f"Add {user} to the docker group", f"usermod -aG docker {shlex.quote(user)}", sudo=True))
        steps.append(Step("Verify: docker --version", "docker --version"))
        steps.append(Step("Verify: daemon status", "systemctl is-active docker" if has_systemd else "service docker status",
                          ignore_failure=not has_systemd))
        if opts.get("hello"):
            steps.append(Step("Verify: run hello-world", "docker run --rm hello-world", sudo=True))
        plan.est_disk = "≈ 400–700 MB (packages and dependencies like containerd/runc)"
        plan.changes = [f"Installs: {', '.join(pkgs)} (plus dependencies, and the compose plugin if available)",
                        "Enables the docker service at boot and starts it" if has_systemd else
                        "Starts the docker service (no systemd)",
                        "Creates the `docker` group (nobody is added unless you opt in below)",
                        "No third-party repositories are added; your distro's own repositories are used"]
        plan.warnings = ["Members of the docker group effectively have root access on the server."] if opts.get("group") else []
    else:
        steps.append(Step("Verify: nginx -v", 'export PATH="$PATH:/usr/sbin:/usr/local/sbin"; nginx -v 2>&1', sudo=False))
        steps.append(Step("Verify: config test", 'export PATH="$PATH:/usr/sbin:/usr/local/sbin"; nginx -t 2>&1', sudo=True))
        if has_systemd:
            steps.append(Step("Verify: service status", "systemctl is-active nginx"))
        plan.est_disk = "≈ 5–20 MB"
        plan.changes = ["Installs: nginx (plus dependencies)",
                        "Enables the nginx service at boot and starts it" if has_systemd else "Starts nginx (no systemd)",
                        "Debian/Ubuntu start nginx with a default welcome site on port 80",
                        "Does not change firewall rules — open ports 80/443 yourself if needed",
                        "No third-party repositories are added"]
    plan.steps = steps
    return plan
