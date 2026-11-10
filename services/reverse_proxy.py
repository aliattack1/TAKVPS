"""Reverse-proxy and SSL manager (Caddy / Traefik).

Detects which reverse proxy is installed (nginx already handled by an earlier module;
this adds Caddy and Traefik) and provides config templates + status checks.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProxyInfo:
    """Which reverse proxy is installed."""
    kind: str = ""        # "caddy" | "traefik" | "nginx" | ""
    version: str = ""
    running: bool = False


def detect_command() -> str:
    """Check which reverse proxy is installed and running."""
    return (
        "echo '##CADDY'; command -v caddy && caddy version 2>/dev/null | head -1; "
        "echo '##TRAEFIK'; command -v traefik && traefik version 2>/dev/null | head -1; "
        "echo '##NGINX'; command -v nginx && nginx -v 2>&1 | head -1; "
        "echo '##STATUS'; systemctl is-active caddy traefik nginx 2>/dev/null"
    )


def parse_detect(text: str) -> ProxyInfo:
    """Parse the detect command output."""
    info = ProxyInfo()
    section = ""
    statuses = []
    for line in text.splitlines():
        if line.startswith("##"):
            section = line[2:].strip().lower()
            continue
        if section == "caddy" and line.strip() and not info.kind:
            info.kind = "caddy"
            parts = line.split()
            if len(parts) > 1 and parts[0] == "caddy":
                info.version = parts[1] if len(parts) > 1 else ""
            else:
                info.version = line.strip()
        elif section == "traefik" and line.strip() and not info.kind:
            info.kind = "traefik"
            info.version = line.strip()
        elif section == "nginx" and line.strip() and not info.kind:
            info.kind = "nginx"
            info.version = line.strip()
        elif section == "status":
            statuses.append(line.strip())
    # Check if our proxy is running
    if info.kind and statuses:
        info.running = "active" in statuses[statuses.index(info.kind)] if info.kind in statuses else False
    return info


# ---- Caddy templates ----

CADDY_STATIC = """# Static site
{domain} {{
    root * {doc_root}
    file_server
    encode gzip
}}
"""

CADDY_REVERSE_PROXY = """# Reverse proxy
{domain} {{
    reverse_proxy {backend_host}:{backend_port}
}}
"""

CADDY_PHP = """# PHP-FPM site
{domain} {{
    root * {doc_root}
    php_fastcgi {php_host}:{php_port}
    file_server
}}
"""

# ---- Traefik templates ----

TRAEFIK_STATIC_LABELS = """# Traefik labels for a static site (Docker)
- "traefik.enable=true"
- "traefik.http.routers.{name}.rule=Host(`{domain}`)"
- "traefik.http.routers.{name}.entrypoints=websecure"
- "traefik.http.routers.{name}.tls=true"
- "traefik.http.services.{name}.loadbalancer.server.port=80"
"""

TRAEFIK_PROXY_LABELS = """# Traefik labels for a reverse proxy (Docker)
- "traefik.enable=true"
- "traefik.http.routers.{name}.rule=Host(`{domain}`)"
- "traefik.http.routers.{name}.entrypoints=websecure"
- "traefik.http.routers.{name}.tls=true"
- "traefik.http.services.{name}.loadbalancer.server.port={port}"
"""


def caddy_reload_command() -> str:
    return "caddy reload --config /etc/caddy/Caddyfile 2>&1"


def caddy_validate_command() -> str:
    return "caddy validate --config /etc/caddy/Caddyfile 2>&1"


def caddy_config_path() -> str:
    return "/etc/caddy/Caddyfile"


def traefik_config_path() -> str:
    return "/etc/traefik/traefik.yml"


def template_for(kind: str, template: str, **kwargs) -> str:
    """Fill in a template with the given kwargs."""
    if kind == "caddy":
        templates = {"static": CADDY_STATIC, "proxy": CADDY_REVERSE_PROXY, "php": CADDY_PHP}
        tmpl = templates.get(template, CADDY_STATIC)
    elif kind == "traefik":
        templates = {"static": TRAEFIK_STATIC_LABELS, "proxy": TRAEFIK_PROXY_LABELS}
        tmpl = templates.get(template, TRAEFIK_STATIC_LABELS)
    else:
        return ""
    try:
        return tmpl.format(**kwargs)
    except KeyError:
        return tmpl
