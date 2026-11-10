"""Nginx: detection, sites, templates, certbot plan. Pure, no UI."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Optional

from services import packages as P
from services.runner import Step

PATH = 'export PATH="$PATH:/usr/sbin:/usr/local/sbin"; '
DETECT_CMD = (PATH + "if command -v nginx >/dev/null 2>&1; then echo '##INSTALLED'; nginx -v 2>&1; "
              "echo '##ACTIVE'; systemctl is-active nginx 2>/dev/null || echo unknown; "
              "echo '##CERTBOT'; command -v certbot >/dev/null 2>&1 && echo yes || echo no; else echo '##MISSING'; fi")
TEST_CMD = PATH + "nginx -t 2>&1"
SITES_CMD = ("if [ -d /etc/nginx/sites-available ]; then echo '##SITES'; ls -1 /etc/nginx/sites-available 2>/dev/null; "
             "echo '##ENABLED'; ls -1 /etc/nginx/sites-enabled 2>/dev/null; "
             "else echo '##CONFD'; ls -1 /etc/nginx/conf.d 2>/dev/null; fi")
PORTS_CMD = "ss -ltnp 2>/dev/null | grep -i nginx || echo 'No listening nginx sockets found (or not enough privileges).'"
ACCESS_LOG = "/var/log/nginx/access.log"
ERROR_LOG = "/var/log/nginx/error.log"
_SITE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOST = re.compile(r"^(_|[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")
_EMAIL = re.compile(r"^[^@\s;|&$`]+@[^@\s;|&$`]+\.[A-Za-z]{2,}$")


@dataclass
class NginxState:
    installed: bool = False
    version: str = ""
    active: str = "unknown"
    certbot: bool = False


def parse_detect(text: str) -> NginxState:
    if "##INSTALLED" not in text:
        return NginxState()
    sec: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:]
            sec[cur] = []
        elif cur and ln.strip():
            sec[cur].append(ln.strip())
    ver = (sec.get("INSTALLED") or [""])[0]
    m = re.search(r"nginx/([\w.]+)", ver)
    return NginxState(True, m.group(1) if m else ver, (sec.get("ACTIVE") or ["unknown"])[0],
                      (sec.get("CERTBOT") or ["no"])[0] == "yes")


@dataclass
class Site:
    name: str
    enabled: bool
    layout: str                 # "sites" (sites-available/enabled) | "confd"
    path: str                   # the file that currently exists (edit this one)


def parse_sites(text: str) -> tuple[list[Site], str]:
    sec: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:]
            sec[cur] = []
        elif cur and ln.strip():
            sec[cur].append(ln.strip())
    if "SITES" in sec:
        en = set(sec.get("ENABLED", []))
        sites = [Site(n, n in en, "sites", f"/etc/nginx/sites-available/{n}") for n in sec["SITES"]]
        return sorted(sites, key=lambda s: s.name.lower()), "sites"
    sites = []
    for n in sec.get("CONFD", []):
        if n.endswith(".conf"):
            sites.append(Site(n, True, "confd", f"/etc/nginx/conf.d/{n}"))
        elif n.endswith(".conf.disabled"):
            sites.append(Site(n[:-9], False, "confd", f"/etc/nginx/conf.d/{n}"))
    return sorted(sites, key=lambda s: s.name.lower()), "confd"


def valid_site_name(name: str) -> bool:
    return bool(_SITE.match(name)) and ".." not in name and len(name) < 120


def site_path(layout: str, name: str) -> str:
    if not valid_site_name(name):
        raise ValueError("Use letters, digits and . _ - only.")
    if layout == "sites":
        return f"/etc/nginx/sites-available/{name}"
    return f"/etc/nginx/conf.d/{name if name.endswith('.conf') else name + '.conf'}"


def toggle_commands(site: Site, enable: bool) -> tuple[str, str]:
    """(do, undo) shell commands to enable/disable a site; undo restores the previous state."""
    if not valid_site_name(site.name):
        raise ValueError("Invalid site name.")
    n = shlex.quote(site.name)
    if site.layout == "sites":
        link = f"ln -s /etc/nginx/sites-available/{n} /etc/nginx/sites-enabled/{n}"
        unlink = f"rm -f /etc/nginx/sites-enabled/{n}"
        return (link, unlink) if enable else (unlink, link)
    on, off = f"/etc/nginx/conf.d/{n}", f"/etc/nginx/conf.d/{shlex.quote(site.name + '.disabled')}"
    return (f"mv {off} {on}", f"mv {on} {off}") if enable else (f"mv {on} {off}", f"mv {off} {on}")


def reload_command(has_systemd: bool = True) -> str:
    """Always tests the config first; reloads only if the test passes."""
    return PATH + "nginx -t && " + ("systemctl reload nginx" if has_systemd else "nginx -s reload")


# ------------------------------------------------------------------ templates
TEMPLATES = {"static": "Static website", "proxy": "Reverse proxy", "php": "PHP-FPM site"}


def _check_host(name: str) -> str:
    if not _HOST.match(name.strip()):
        raise ValueError("Enter a valid server name, e.g. example.com")
    return name.strip()


def _check_path(p: str) -> str:
    p = p.strip()
    if not p.startswith("/") or re.search(r"[\s;{}$\"'`\\]", p):
        raise ValueError("Use an absolute path without spaces or special characters.")
    return p


def _check_upstream(u: str) -> str:
    u = u.strip()
    if not re.match(r"^https?://[\w.\-\[\]:]+(/[\w./-]*)?$", u):
        raise ValueError("Upstream must look like http://127.0.0.1:3000")
    return u


def render_template(kind: str, server_name: str, target: str, php_socket: str = "/run/php/php-fpm.sock") -> str:
    name = _check_host(server_name)
    head = f"server {{\n    listen 80;\n    listen [::]:80;\n    server_name {name};\n\n"
    if kind == "static":
        root = _check_path(target)
        return (head + f"    root {root};\n    index index.html index.htm;\n\n    location / {{\n"
                "        try_files $uri $uri/ =404;\n    }\n}\n")
    if kind == "proxy":
        up = _check_upstream(target)
        return (head + f"    location / {{\n        proxy_pass {up};\n        proxy_http_version 1.1;\n"
                "        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n"
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                "        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection \"upgrade\";\n"
                "    }\n}\n")
    if kind == "php":
        root, sock = _check_path(target), _check_path(php_socket)
        return (head + f"    root {root};\n    index index.php index.html;\n\n    location / {{\n"
                "        try_files $uri $uri/ /index.php?$query_string;\n    }\n\n    location ~ \\.php$ {\n"
                f"        include snippets/fastcgi-php.conf;\n        fastcgi_pass unix:{sock};\n    }}\n}}\n")
    raise ValueError("Unknown template.")


# ------------------------------------------------------------------ certbot
def valid_domain(d: str) -> bool:
    return bool(_DOMAIN.match(d.strip()))


def valid_email(e: str) -> bool:
    return bool(_EMAIL.match(e.strip()))


def certbot_steps(pm: str, installed: bool, domains: list[str], email: str) -> list[Step]:
    if not domains or not all(valid_domain(d) for d in domains):
        raise ValueError("Enter valid domain names (e.g. example.com).")
    if not valid_email(email):
        raise ValueError("Enter a valid email address.")
    pkgs = {"apt": ["certbot", "python3-certbot-nginx"], "dnf": ["certbot", "python3-certbot-nginx"],
            "yum": ["certbot", "python3-certbot-nginx"], "pacman": ["certbot", "certbot-nginx"],
            "apk": ["certbot", "certbot-nginx"], "zypper": ["certbot", "python3-certbot-nginx"]}[pm]
    steps = []
    if not installed:
        steps += [Step("Refresh package index", P.cmd_refresh(pm), sudo=True),
                  Step(f"Install {', '.join(pkgs)}", P.cmd_install(pm, pkgs), sudo=True)]
    d_args = " ".join(f"-d {shlex.quote(d.strip())}" for d in domains)
    steps.append(Step("Request certificate", f"certbot --nginx {d_args} --non-interactive --agree-tos "
                      f"-m {shlex.quote(email.strip())} --redirect", sudo=True))
    return steps
