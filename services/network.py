"""Listening ports, connections and diagnostic tool commands. Pure."""
from __future__ import annotations

import re
import shlex
from collections import Counter
from dataclasses import dataclass

LISTEN_CMD = "ss -tulpn 2>/dev/null"
CONNS_CMD = "ss -tnp state established 2>/dev/null"
PUBLIC_IP_CMD = "curl -s --max-time 8 https://api.ipify.org || curl -s --max-time 8 https://ifconfig.me"
_HOST = re.compile(r"^(?!-)[A-Za-z0-9.-]{1,253}$")
_V6 = re.compile(r"^[0-9A-Fa-f:]{2,45}$")


@dataclass
class ListenPort:
    proto: str
    addr: str
    port: int
    process: str = ""
    pid: int = 0

    @property
    def public(self) -> bool:
        return not (self.addr.startswith("127.") or self.addr in ("::1", "[::1]", "localhost"))


def _split_addr(a: str) -> tuple[str, int]:
    a = re.sub(r"%[^\]:]+", "", a)                      # drop the %iface scope id
    host, _, port = a.rpartition(":")
    return host.strip("[]") or "*", int(port) if port.isdigit() else 0


def parse_listen(text: str) -> list[ListenPort]:
    out = []
    for ln in text.splitlines():
        t = ln.split(None, 6)
        if len(t) < 6 or t[0] in ("Netid", "State") or t[0] not in ("tcp", "udp", "tcp6", "udp6"):
            continue
        if t[1] not in ("LISTEN", "UNCONN"):
            continue
        host, port = _split_addr(t[4])
        m = re.search(r'\("([^"]+)",pid=(\d+)', t[6]) if len(t) > 6 else None
        out.append(ListenPort(t[0].rstrip("6"), host, port, m.group(1) if m else "", int(m.group(2)) if m else 0))
    seen, uniq = set(), []
    for p in sorted(out, key=lambda x: (x.port, x.proto, x.addr)):
        k = (p.proto, p.port, p.addr)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def parse_conns(text: str) -> list[tuple[str, int, str]]:
    """-> [(remote_ip, remote_port, process)] for established TCP connections."""
    out = []
    for ln in text.splitlines():
        t = ln.split(None, 5)
        if len(t) < 4 or not t[0].isdigit():
            continue
        host, port = _split_addr(t[3])
        m = re.search(r'\("([^"]+)"', t[4] if len(t) > 4 else "")
        out.append((host, port, m.group(1) if m else ""))
    return out


def top_remotes(conns: list[tuple[str, int, str]], n: int = 10) -> list[tuple[str, int]]:
    return Counter(h for h, _p, _s in conns).most_common(n)


def valid_host(h: str) -> bool:
    h = h.strip()
    return bool(_HOST.match(h) or _V6.match(h)) and not h.startswith("-")


def _h(h: str) -> str:
    if not valid_host(h):
        raise ValueError("Enter a valid host name or IP address.")
    return shlex.quote(h.strip())


def ping_command(h: str) -> str:
    return f"ping -c 4 -W 3 {_h(h)} 2>&1"


def traceroute_command(h: str) -> str:
    host = _h(h)
    return (f"if command -v traceroute >/dev/null 2>&1; then traceroute -m 15 -w 2 {host} 2>&1; "
            f"elif command -v tracepath >/dev/null 2>&1; then tracepath -m 15 {host} 2>&1; "
            "else echo 'traceroute/tracepath is not installed on this server.'; fi")


def dns_command(h: str) -> str:
    host = _h(h)
    return (f"if command -v dig >/dev/null 2>&1; then dig +short {host} A; dig +short {host} AAAA; "
            f"elif command -v nslookup >/dev/null 2>&1; then nslookup {host} 2>&1; else getent ahosts {host}; fi")
