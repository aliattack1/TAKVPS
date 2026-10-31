"""Firewall parsing and SSH-lockout guards for ufw and firewalld (iptables/nft: read-only). Pure."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Optional

DETECT_CMD = "for t in ufw firewall-cmd nft iptables; do command -v $t >/dev/null 2>&1 && echo $t; done; true"
UFW_STATUS = "ufw status numbered"
FIREWALLD_STATE = "firewall-cmd --state 2>&1"
FIREWALLD_LIST = "firewall-cmd --list-all 2>&1"
IPTABLES_LIST = "iptables -S 2>&1 | head -n 80"
NFT_LIST = "nft list ruleset 2>&1 | head -n 120"


def detect_backend(tools: str) -> str:
    t = set(tools.split())
    for b, name in (("ufw", "ufw"), ("firewalld", "firewall-cmd"), ("nft", "nft"), ("iptables", "iptables")):
        if name in t:
            return b
    return ""


@dataclass
class UfwRule:
    num: int
    to: str
    action: str
    direction: str
    frm: str

    @property
    def v6(self) -> bool:
        return "(v6)" in self.to or "(v6)" in self.frm


@dataclass
class UfwStatus:
    active: bool = False
    rules: list[UfwRule] = field(default_factory=list)


_RULE = re.compile(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)(?:\s+(IN|OUT|FWD))?\s+(.+?)\s*$")


def parse_ufw(text: str) -> UfwStatus:
    st = UfwStatus(active="status: active" in text.lower())
    for ln in text.splitlines():
        m = _RULE.match(ln.strip())
        if m:
            st.rules.append(UfwRule(int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4) or "IN", m.group(5).strip()))
    return st


def covers_port(to: str, port: int) -> bool:
    """Does a ufw 'To' field (22/tcp, OpenSSH, 80,443/tcp, 1000:2000/udp, Anywhere) include `port`?"""
    t = to.replace("(v6)", "").strip()
    if not t:
        return False
    if t.lower() == "anywhere":
        return True
    first = t.split()[0]
    if first.lower() in ("openssh", "ssh"):
        return port == 22
    for part in first.split("/")[0].split(","):
        if ":" in part:
            a, _, b = part.partition(":")
            if a.isdigit() and b.isdigit() and int(a) <= port <= int(b):
                return True
        elif part.isdigit() and int(part) == port:
            return True
    return False


def ufw_delete_guard(st: UfwStatus, num: int, ssh_port: int) -> Optional[str]:
    """Message if deleting rule `num` could lock out SSH; else None."""
    target = next((r for r in st.rules if r.num == num), None)
    if target is None:
        return "That rule no longer exists. Refresh the list."
    if st.active and target.action in ("ALLOW", "LIMIT") and covers_port(target.to, ssh_port):
        others = [r for r in st.rules if r.num != num and r.v6 == target.v6 and r.action in ("ALLOW", "LIMIT")
                  and covers_port(r.to, ssh_port)]
        if not others:
            return (f"This rule keeps your SSH connection (port {ssh_port}) open. Deleting it while the firewall is "
                    "active would lock you out. Add another rule for that port first.")
    return None


def ufw_enable_plan(st: UfwStatus, ssh_port: int) -> list[str]:
    cmds = []
    if not any(r.action in ("ALLOW", "LIMIT") and not r.v6 and covers_port(r.to, ssh_port) for r in st.rules):
        cmds.append(f"ufw allow {ssh_port}/tcp")
    cmds.append("ufw --force enable")
    return cmds


def _port_spec(spec: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,5})(?::(\d{1,5}))?", spec.strip())
    if not m:
        raise ValueError("Enter a port (e.g. 8080) or a range (e.g. 6000:6100).")
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    if not (1 <= a <= 65535 and 1 <= b <= 65535 and a <= b):
        raise ValueError("Ports must be between 1 and 65535.")
    return a, b


def ufw_add_command(action: str, port_spec: str, proto: str, source: str, ssh_port: int) -> str:
    if action not in ("allow", "deny", "reject", "limit"):
        raise ValueError("Unknown action.")
    if proto not in ("tcp", "udp", "any"):
        raise ValueError("Protocol must be tcp, udp or any.")
    a, b = _port_spec(port_spec)
    if action in ("deny", "reject") and a <= ssh_port <= b and proto in ("tcp", "any"):
        raise ValueError(f"That would block port {ssh_port}, which your SSH connection uses.")
    src = ""
    if source.strip():
        try:
            src = str(ipaddress.ip_network(source.strip(), strict=False))
        except ValueError as e:
            raise ValueError("Source must be an IP address or CIDR, e.g. 203.0.113.5 or 10.0.0.0/24.") from e
    p = f"{a}" if a == b else f"{a}:{b}"
    pr = "" if proto == "any" else f"/{proto}"
    if src:
        return f"ufw {action} from {src} to any port {p}" + ("" if proto == "any" else f" proto {proto}")
    return f"ufw {action} {p}{pr}"


def ufw_delete_command(num: int) -> str:
    return f"ufw --force delete {int(num)}"


@dataclass
class FirewalldInfo:
    running: bool = False
    zone: str = ""
    ports: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)


def parse_firewalld(state: str, listing: str) -> FirewalldInfo:
    info = FirewalldInfo(running=state.strip().lower() == "running")
    for ln in listing.splitlines():
        s = ln.strip()
        if s.endswith("(active)") and not info.zone:
            info.zone = s.split()[0]
        elif s.startswith("ports:"):
            info.ports = s[6:].split()
        elif s.startswith("services:"):
            info.services = s[9:].split()
    return info


def firewalld_command(verb: str, kind: str, value: str, ssh_port: int) -> str:
    """verb add|remove, kind port|service. Persistent + reload. Blocks removing SSH access."""
    if verb not in ("add", "remove") or kind not in ("port", "service"):
        raise ValueError("Unknown action.")
    if kind == "service":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
            raise ValueError("Invalid service name.")
        if verb == "remove" and value == "ssh" and ssh_port == 22:
            raise ValueError("Removing the ssh service would lock you out (your connection uses port 22).")
        arg = f"--{verb}-service={value}"
    else:
        m = re.fullmatch(r"(\d{1,5}(?:-\d{1,5})?)/(tcp|udp)", value)
        if not m:
            raise ValueError("Use the form 8080/tcp or 6000-6100/udp.")
        a, _, b = m.group(1).partition("-")
        lo, hi = int(a), int(b or a)
        if not (1 <= lo <= hi <= 65535):
            raise ValueError("Ports must be between 1 and 65535.")
        if verb == "remove" and m.group(2) == "tcp" and lo <= ssh_port <= hi:
            raise ValueError(f"Removing this would close port {ssh_port}, which your SSH connection uses.")
        arg = f"--{verb}-port={value}"
    return f"firewall-cmd --permanent {arg} && firewall-cmd --reload"
