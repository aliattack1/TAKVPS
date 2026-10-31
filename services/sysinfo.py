"""OS / privilege / capability detection. No UI code."""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing asyncssh in pure-parsing tests
    from core.ssh_client import SSHClient

_SCRIPT = r"""
echo '##HOST'; hostname
echo '##OS'; cat /etc/os-release 2>/dev/null
echo '##KERNEL'; uname -sr
echo '##ARCH'; uname -m
echo '##UPTIME'; cat /proc/uptime 2>/dev/null
echo '##LOAD'; cat /proc/loadavg 2>/dev/null
echo '##MEM'; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo 2>/dev/null
echo '##UID'; id -u
echo '##HASSUDO'; command -v sudo >/dev/null 2>&1 && echo yes || echo no
echo '##SUDOOK'; sudo -n true >/dev/null 2>&1 && echo yes || echo no
echo '##PKG'; for p in apt-get dnf yum pacman apk zypper; do command -v $p >/dev/null 2>&1 && echo $p && break; done
echo '##SYSTEMD'; [ -d /run/systemd/system ] && echo yes || echo no
"""
SYSINFO_CMD = "sh -c " + shlex.quote(_SCRIPT)


@dataclass
class SysInfo:
    hostname: str = ""
    distro: str = ""
    kernel: str = ""
    arch: str = ""
    uptime_s: float = 0.0
    load: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mem_total_kb: int = 0
    mem_avail_kb: int = 0
    uid: int = -1
    sudo: str = "none"          # root | passwordless | password | none
    package_manager: str = ""
    has_systemd: bool = False

    @property
    def is_root(self) -> bool:
        return self.uid == 0


def _sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("##"):
            cur = line[2:].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _os_release(block: str) -> str:
    kv = {}
    for line in block.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip().strip('"').strip("'")
    return kv.get("PRETTY_NAME") or kv.get("NAME", "")


def parse_sysinfo(text: str) -> SysInfo:
    s = _sections(text)
    info = SysInfo(hostname=s.get("HOST", ""), distro=_os_release(s.get("OS", "")),
                   kernel=s.get("KERNEL", ""), arch=s.get("ARCH", ""),
                   package_manager=s.get("PKG", "").split("\n")[0],
                   has_systemd=s.get("SYSTEMD") == "yes")
    try:
        info.uptime_s = float(s.get("UPTIME", "").split()[0])
    except (IndexError, ValueError):
        pass
    try:
        a, b, c = (float(x) for x in s.get("LOAD", "").split()[:3])
        info.load = (a, b, c)
    except ValueError:
        pass
    for line in s.get("MEM", "").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            if parts[0] == "MemTotal":
                info.mem_total_kb = int(parts[1])
            elif parts[0] == "MemAvailable":
                info.mem_avail_kb = int(parts[1])
    try:
        info.uid = int(s.get("UID", "-1"))
    except ValueError:
        pass
    if info.uid == 0:
        info.sudo = "root"
    elif s.get("SUDOOK") == "yes":
        info.sudo = "passwordless"
    elif s.get("HASSUDO") == "yes":
        info.sudo = "password"
    return info


async def fetch(ssh: "SSHClient") -> SysInfo:
    r = await ssh.run(SYSINFO_CMD, timeout=20)
    return parse_sysinfo(r.stdout)
