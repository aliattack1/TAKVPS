"""(C4) — WireGuard VPN manager.

Manages a WireGuard setup on the server: generate keys, configure peers,
show connection status, start/stop the interface. Client-only — just edits
`/etc/wireguard/wg0.conf` over SSH.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WireGuardPeer:
    """One peer in the WireGuard config."""
    public_key: str
    allowed_ips: str = ""
    endpoint: str = ""
    preshared_key: str = ""
    persistent_keepalive: int = 0
    comment: str = ""


@dataclass
class WireGuardConfig:
    """Parsed WireGuard config (wg0.conf)."""
    interface_private_key: str = ""
    interface_address: str = ""
    interface_listen_port: int = 0
    interface_dns: str = ""
    interface_post_up: str = ""
    interface_post_down: str = ""
    peers: list[WireGuardPeer] = field(default_factory=list)


def parse_config(text: str) -> WireGuardConfig:
    """Parse a WireGuard config file (INI format)."""
    cfg = WireGuardConfig()
    section = None
    current_peer: Optional[WireGuardPeer] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            if section == "peer":
                current_peer = WireGuardPeer(public_key="")
                cfg.peers.append(current_peer)
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if section == "interface":
            if key == "privatekey":
                cfg.interface_private_key = value
            elif key == "address":
                cfg.interface_address = value
            elif key == "listenport":
                cfg.interface_listen_port = int(value) if value.isdigit() else 0
            elif key == "dns":
                cfg.interface_dns = value
            elif key == "postup":
                cfg.interface_post_up = value
            elif key == "postdown":
                cfg.interface_post_down = value
        elif section == "peer" and current_peer:
            if key == "publickey":
                current_peer.public_key = value
            elif key == "allowedips":
                current_peer.allowed_ips = value
            elif key == "endpoint":
                current_peer.endpoint = value
            elif key == "presharedkey":
                current_peer.preshared_key = value
            elif key == "persistentkeepalive":
                current_peer.persistent_keepalive = int(value) if value.isdigit() else 0
    return cfg


def build_config(cfg: WireGuardConfig) -> str:
    """Serialize a WireGuardConfig back to INI text."""
    lines = ["[Interface]"]
    if cfg.interface_private_key:
        lines.append(f"PrivateKey = {cfg.interface_private_key}")
    if cfg.interface_address:
        lines.append(f"Address = {cfg.interface_address}")
    if cfg.interface_listen_port:
        lines.append(f"ListenPort = {cfg.interface_listen_port}")
    if cfg.interface_dns:
        lines.append(f"DNS = {cfg.interface_dns}")
    if cfg.interface_post_up:
        lines.append(f"PostUp = {cfg.interface_post_up}")
    if cfg.interface_post_down:
        lines.append(f"PostDown = {cfg.interface_post_down}")
    for peer in cfg.peers:
        lines.append("")
        lines.append("[Peer]")
        if peer.public_key:
            lines.append(f"PublicKey = {peer.public_key}")
        if peer.allowed_ips:
            lines.append(f"AllowedIPs = {peer.allowed_ips}")
        if peer.endpoint:
            lines.append(f"Endpoint = {peer.endpoint}")
        if peer.preshared_key:
            lines.append(f"PresharedKey = {peer.preshared_key}")
        if peer.persistent_keepalive:
            lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")
    return "\n".join(lines) + "\n"


def detect_command() -> str:
    """Check if WireGuard is installed."""
    return "command -v wg && command -v wg-quick"


def status_command(iface: str = "wg0") -> str:
    """Get the WireGuard interface status."""
    return f"wg show {shlex.quote(iface)} 2>/dev/null || echo 'NOT_RUNNING'"


def up_command(iface: str = "wg0") -> str:
    """Bring the interface up."""
    return f"wg-quick up {shlex.quote(iface)}"


def down_command(iface: str = "wg0") -> str:
    """Bring the interface down."""
    return f"wg-quick down {shlex.quote(iface)}"


def read_config_command(iface: str = "wg0") -> str:
    """Read the config file (may need sudo)."""
    return f"cat /etc/wireguard/{shlex.quote(iface)}.conf 2>/dev/null || sudo cat /etc/wireguard/{shlex.quote(iface)}.conf"


def write_config_command(iface: str = "wg0") -> str:
    """Write the config file via sudo install (root-owned)."""
    return f"sudo tee /etc/wireguard/{shlex.quote(iface)}.conf > /dev/null"


def genkey_command() -> str:
    """Generate a new WireGuard private key on the server."""
    return "wg genkey"


def pubkey_command() -> str:
    """Derive the public key from a private key (reads private key on stdin)."""
    return "wg pubkey"


def parse_status(text: str) -> dict:
    """Parse `wg show` output into a status dict."""
    status: dict = {"running": False, "peers": []}
    if "NOT_RUNNING" in text:
        return status
    status["running"] = True
    current_peer: Optional[dict] = None
    for line in text.splitlines():
        if line.startswith("  peer:"):
            current_peer = {"key": line.split(":", 1)[1].strip()}
            status["peers"].append(current_peer)
        elif current_peer and ":" in line:
            key, _, val = line.strip().partition(":")
            current_peer[key.strip()] = val.strip()
        elif ":" in line and not current_peer:
            key, _, val = line.partition(":")
            status[key.strip().lower()] = val.strip()
    return status
