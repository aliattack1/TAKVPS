"""Package-manager adapters (apt, dnf/yum, pacman, apk, zypper): commands + parsers. Pure."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Optional

SUPPORTED = ("apt", "dnf", "yum", "pacman", "apk", "zypper")
_NAME = re.compile(r"^[\w.+:@-]+$")
_APT_ENV = "DEBIAN_FRONTEND=noninteractive "


@dataclass
class Pkg:
    name: str
    version: str = ""
    desc: str = ""


@dataclass
class Upgrade:
    name: str
    new: str = ""
    old: str = ""
    security: bool = False


def detect(raw: str) -> Optional[str]:
    n = {"apt-get": "apt", "apt": "apt"}.get(raw.strip(), raw.strip())
    return n if n in SUPPORTED else None


def valid_pkg(name: str) -> bool:
    return bool(_NAME.match(name)) and len(name) < 200


def _names(names: list[str]) -> str:
    if not names or not all(valid_pkg(n) for n in names):
        raise ValueError("Invalid package name.")
    return " ".join(shlex.quote(n) for n in names)


# ------------------------------------------------------------------ commands
def cmd_installed(pm: str) -> str:
    return {"apt": r"dpkg-query -W -f='${binary:Package}\t${Version}\n'",
            "dnf": r"rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n'", "yum": r"rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n'",
            "zypper": r"rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n'", "pacman": "pacman -Q",
            "apk": "apk info -v"}[pm]


def cmd_search(pm: str, query: str) -> str:
    q = shlex.quote(query.strip())
    if not query.strip():
        raise ValueError("Enter something to search for.")
    return {"apt": f"apt-cache search --names-only -- {q} | head -n 100",
            "dnf": f"dnf -q search {q} 2>/dev/null | head -n 150", "yum": f"yum -q search {q} 2>/dev/null | head -n 150",
            "zypper": f"zypper -q search {q} 2>/dev/null | head -n 150", "pacman": f"pacman -Ss -- {q} | head -n 200",
            "apk": f"apk search -v -- {q} | head -n 100"}[pm]


def script_upgradable(pm: str) -> str:
    return {"apt": "apt list --upgradable 2>/dev/null",
            "dnf": "dnf -q check-update 2>/dev/null; echo '##SEC'; dnf -q --security check-update 2>/dev/null; true",
            "yum": "yum -q check-update 2>/dev/null; echo '##SEC'; yum -q --security check-update 2>/dev/null; true",
            "zypper": "zypper -q list-updates 2>/dev/null; true", "pacman": "pacman -Qu 2>/dev/null; true",
            "apk": "apk version -l '<' 2>/dev/null; true"}[pm]


def cmd_install(pm: str, names: list[str]) -> str:
    n = _names(names)
    return {"apt": f"{_APT_ENV}apt-get install -y {n}", "dnf": f"dnf install -y {n}", "yum": f"yum install -y {n}",
            "zypper": f"zypper -n install {n}", "pacman": f"pacman -S --noconfirm {n}", "apk": f"apk add {n}"}[pm]


def cmd_remove(pm: str, names: list[str]) -> str:
    n = _names(names)
    return {"apt": f"{_APT_ENV}apt-get remove -y {n}", "dnf": f"dnf remove -y {n}", "yum": f"yum remove -y {n}",
            "zypper": f"zypper -n remove {n}", "pacman": f"pacman -R --noconfirm {n}", "apk": f"apk del {n}"}[pm]


def cmd_upgrade(pm: str, names: list[str]) -> str:
    n = _names(names)
    return {"apt": f"{_APT_ENV}apt-get install --only-upgrade -y {n}", "dnf": f"dnf upgrade -y {n}",
            "yum": f"yum update -y {n}", "zypper": f"zypper -n update {n}", "pacman": f"pacman -S --noconfirm {n}",
            "apk": f"apk add -u {n}"}[pm]


def cmd_upgrade_all(pm: str) -> str:
    return {"apt": f"{_APT_ENV}apt-get upgrade -y", "dnf": "dnf upgrade -y", "yum": "yum update -y",
            "zypper": "zypper -n update", "pacman": "pacman -Syu --noconfirm", "apk": "apk upgrade"}[pm]


def cmd_refresh(pm: str) -> str:
    return {"apt": "apt-get update", "dnf": "dnf makecache", "yum": "yum makecache", "zypper": "zypper -n refresh",
            "pacman": "pacman -Sy", "apk": "apk update"}[pm]


def supports_security_split(pm: str) -> bool:
    return pm in ("apt", "dnf", "yum", "zypper")


def cmd_security(pm: str, names: list[str]) -> str:
    """Install security updates only."""
    if pm == "apt":
        return cmd_upgrade("apt", names)
    return {"dnf": "dnf upgrade --security -y", "yum": "yum update --security -y",
            "zypper": "zypper -n patch --category security"}[pm]


def cmd_changelog(pm: str, name: str) -> Optional[str]:
    if pm != "apt":
        return None
    return f"apt-get changelog {_names([name])} 2>&1 | head -n 60"


REBOOT_CMD = ("if [ -f /var/run/reboot-required ]; then echo yes; "
              "elif command -v needs-restarting >/dev/null 2>&1; then needs-restarting -r >/dev/null 2>&1 && echo no || echo yes; "
              "else echo unknown; fi")


def parse_reboot(out: str) -> Optional[bool]:
    v = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return True if v == "yes" else False if v == "no" else None


# ------------------------------------------------------------------ parsers
_APK_VER = re.compile(r"^(.+?)-(\d[\w.+~-]*)$")


def parse_installed(pm: str, text: str) -> list[Pkg]:
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if pm in ("apt", "dnf", "yum", "zypper"):
            n, _, v = ln.partition("\t")
            out.append(Pkg(n.strip(), v.strip()))
        elif pm == "pacman":
            p = ln.split()
            if len(p) >= 2:
                out.append(Pkg(p[0], p[1]))
        elif pm == "apk":
            m = _APK_VER.match(ln.strip())
            out.append(Pkg(m.group(1), m.group(2)) if m else Pkg(ln.strip()))
    return sorted(out, key=lambda p: p.name.lower())


def parse_search(pm: str, text: str) -> list[Pkg]:
    out = []
    lines = text.splitlines()
    if pm == "apt":
        for ln in lines:
            n, sep, d = ln.partition(" - ")
            if sep and valid_pkg(n.strip()):
                out.append(Pkg(n.strip(), "", d.strip()))
    elif pm in ("dnf", "yum"):
        for ln in lines:
            if " : " in ln and not ln.startswith("="):
                n, _, d = ln.partition(" : ")
                out.append(Pkg(n.strip().rsplit(".", 1)[0] if "." in n.strip() else n.strip(), "", d.strip()))
    elif pm == "pacman":
        for i, ln in enumerate(lines):
            if ln and not ln.startswith(" ") and "/" in ln.split()[0]:
                p = ln.split()
                out.append(Pkg(p[0].split("/", 1)[1], p[1] if len(p) > 1 else "",
                               lines[i + 1].strip() if i + 1 < len(lines) else ""))
    elif pm == "apk":
        for ln in lines:
            m = _APK_VER.match(ln.strip())
            if m:
                out.append(Pkg(m.group(1), m.group(2)))
    elif pm == "zypper":
        for ln in lines:
            p = [x.strip() for x in ln.split("|")]
            if len(p) >= 4 and p[1] and p[1] != "Name":
                out.append(Pkg(p[1], "", p[2]))
    return out


_APT_UP = re.compile(r"^([^/\s]+)/(\S+)\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]")


def parse_upgradable(pm: str, text: str) -> list[Upgrade]:
    out: list[Upgrade] = []
    if pm == "apt":
        for ln in text.splitlines():
            m = _APT_UP.match(ln.strip())
            if m:
                out.append(Upgrade(m.group(1), m.group(3), m.group(4), "security" in m.group(2).lower()))
    elif pm in ("dnf", "yum"):
        allp, _, sec = text.partition("##SEC")

        def rows(block: str) -> list[tuple[str, str]]:
            res = []
            for ln in block.splitlines():
                p = ln.split()
                if len(p) == 3 and "." in p[0] and not ln.startswith(("Obsolete", " ")):
                    res.append((p[0].rsplit(".", 1)[0], p[1]))
            return res
        sec_names = {n for n, _ in rows(sec)}
        out = [Upgrade(n, v, "", n in sec_names) for n, v in rows(allp)]
    elif pm == "pacman":
        for ln in text.splitlines():
            m = re.match(r"^(\S+)\s+(\S+)\s+->\s+(\S+)", ln)
            if m:
                out.append(Upgrade(m.group(1), m.group(3), m.group(2)))
    elif pm == "apk":
        for ln in text.splitlines():
            m = re.match(r"^(.+?)-(\d\S*)\s+<\s+(\S+)", ln.strip())
            if m:
                out.append(Upgrade(m.group(1), m.group(3), m.group(2)))
    elif pm == "zypper":
        for ln in text.splitlines():
            p = [x.strip() for x in ln.split("|")]
            if len(p) >= 5 and p[0] == "v":
                out.append(Upgrade(p[2], p[4], p[3]))
    return sorted(out, key=lambda u: (not u.security, u.name.lower()))


_LOCK = re.compile(r"Could not get lock|Unable to acquire the dpkg|dpkg was interrupted|Waiting for cache lock|"
                   r"Failed to lock|database lock|is another process|Waiting for process with pid", re.I)


def friendly_hint(output: str) -> Optional[str]:
    if _LOCK.search(output):
        if re.search(r"dpkg was interrupted", output, re.I):
            return ("A previous package operation was interrupted. Open the terminal and run "
                    "`sudo dpkg --configure -a`, then try again.")
        return ("Another package process is running (often automatic updates). Wait a minute or two and try again.")
    return None
