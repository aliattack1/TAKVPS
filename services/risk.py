"""Command risk classifier: safe / writes / destructive / dangerous. Pure Python, no UI.

Conservative by design: anything unrecognised is at least "writes". Reused later by Dry-Run Preview.
This is a safety net for humans reviewing commands, not a sandbox.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from services.files import is_protected

LEVELS = {"safe": 0, "writes": 1, "destructive": 2, "dangerous": 3}


@dataclass
class Risk:
    level: str = "safe"
    reasons: list[str] = field(default_factory=list)
    sensitive: bool = False            # may print secrets (keys, shadow, env)

    def bump(self, level: str, reason: str) -> None:
        if LEVELS[level] > LEVELS[self.level]:
            self.level = level
            self.reasons.insert(0, reason)
        elif LEVELS[level] == LEVELS[self.level] and level != "safe" and reason not in self.reasons:
            self.reasons.append(reason)

    @property
    def explanation(self) -> str:
        return "; ".join(self.reasons[:3]) if self.reasons else "Read-only: it only looks at things."

    @property
    def needs_approval(self) -> bool:
        return self.level != "safe" or self.sensitive


SAFE_CMDS = set("""ls cat head tail less more grep egrep fgrep rg du df free uptime who w whoami id uname hostname
date pwd echo printf wc sort uniq cut tr stat file lsblk lscpu lsof ps pgrep ss netstat ifconfig ping traceroute
tracepath dig nslookup host which whereis type basename dirname realpath readlink env printenv locale lsmod lspci
lsusb dmesg vmstat iostat mpstat sar nproc arch groups last lastb lastlog getent test true false cd export alias
column nl rev tac diff cmp md5sum sha1sum sha256sum sha512sum base64 ncdu tree jq yq top htop btop man tldr""".split())
WRITE_CMDS = set("""mkdir touch cp mv ln install tee chmod chown chgrp useradd groupadd adduser addgroup usermod
groupmod npm pip pip3 pipx gem cargo make cmake gcc tar zip unzip gzip gunzip bzip2 xz rsync scp sftp ssh nano vi
vim emacs sudoedit source . python python3 perl ruby node php bash sh zsh timedatectl hostnamectl sysctl update-grub
logrotate ldconfig fc-cache nginx certbot supervisorctl pm2 snap flatpak""".split())
DESTRUCTIVE_CMDS = {
    "rm": "rm deletes files permanently", "rmdir": "removes directories", "shred": "irrecoverably overwrites files",
    "truncate": "cuts files down, losing data", "unlink": "deletes a file", "kill": "stops processes",
    "pkill": "stops processes by name", "killall": "stops all processes with a name", "umount": "unmounts a filesystem",
    "swapoff": "disables swap", "lvremove": "deletes a logical volume", "userdel": "deletes a user account",
    "groupdel": "deletes a group", "deluser": "deletes a user account", "delgroup": "deletes a group",
    "dd": "dd copies raw data and can overwrite anything", "wipefs": "erases filesystem signatures",
}
DANGEROUS_CMDS = {
    "mkfs": "formats a disk (erases everything on it)", "fdisk": "edits disk partitions", "parted": "edits disk partitions",
    "sfdisk": "edits disk partitions", "cfdisk": "edits disk partitions", "cryptsetup": "changes disk encryption",
    "reboot": "restarts the server and disconnects you", "shutdown": "shuts the server down",
    "poweroff": "powers the server off", "halt": "halts the server", "telinit": "changes the system run level",
    "passwd": "changes a password (could lock you out)", "visudo": "edits sudo permissions",
    "chattr": "changes low-level file protection", "pvremove": "destroys LVM metadata", "vgremove": "destroys an LVM group",
    "mkswap": "formats a swap area", "iptables": "changes firewall rules (can lock you out)",
    "ip6tables": "changes firewall rules (can lock you out)", "nft": "changes firewall rules (can lock you out)",
    "firewall-cmd": "changes firewall rules (can lock you out)",
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "python", "python3", "perl", "ruby", "node", "php"}
DOWNLOADERS = {"curl", "wget", "fetch"}
WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "nice", "ionice", "command", "exec", "stdbuf", "timeout",
            "xargs", "watch", "setsid", "unbuffer"}
SENSITIVE_RE = re.compile(r"(/etc/(shadow|gshadow|sudoers)|id_(rsa|ed25519|ecdsa|dsa)|\.pem$|\.key$|\.env\b|/\.ssh/|"
                          r"/\.aws/|\.netrc|\.pgpass|/proc/[^/]+/environ|\.kube/config|credentials?\b)", re.I)
CRITICAL_FILES = {"/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/fstab", "/etc/group", "/etc/ssh/sshd_config"}
DISK_RE = re.compile(r"^/dev/(sd|hd|vd|xvd|nvme|mmcblk|dm-|mapper/)")
HOME_LIKE = {"~", "~/", "~/*", "$HOME", "${HOME}", "$HOME/", "$HOME/*", "*", "/*", ".", "..", "./*", "../*"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&"}


def classify(cmd: str, _depth: int = 0) -> Risk:
    risk = Risk()
    raw = cmd.strip()
    if not raw:
        return risk
    if re.search(r":\(\)\s*\{", raw):
        risk.bump("dangerous", "looks like a fork bomb that would freeze the server")
    if re.search(r"(^|[\s;|&])mkfs(\.\w+)?\b", raw):
        risk.bump("dangerous", "formats a disk (erases everything on it)")
    if "$(" in raw or "`" in raw:
        risk.bump("writes", "contains command substitution, so its real effect can't be checked")
    try:
        lex = shlex.shlex(raw.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split, lex.commenters = True, ""
        tokens = list(lex)
    except ValueError:
        risk.bump("writes", "couldn't be parsed (unbalanced quotes?) — read it carefully")
        return risk

    segments: list[list[str]] = [[]]
    seps: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in SEPARATORS:
            seps.append(t)
            segments.append([])
        elif re.fullmatch(r"\d*(>>?|>\||>&|&>>?)", t) or t in (">", ">>"):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 1
            if t.endswith("&") and target.isdigit() or target.startswith("&") or target == "/dev/null":
                pass
            elif DISK_RE.match(target):
                risk.bump("dangerous", f"writes straight onto a disk device ({target})")
            elif target in CRITICAL_FILES:
                risk.bump("dangerous", f"overwrites the critical system file {target}")
            else:
                risk.bump("writes", f"writes to a file ({target})")
        elif re.fullmatch(r"\d*<{1,3}", t):
            i += 1
        else:
            segments[-1].append(t)
        i += 1
    segments = [s for s in segments if s]
    if not segments:
        return risk

    # pipe into an interpreter
    for n, sep in enumerate(seps):
        if sep in ("|", "|&") and n + 1 < len(segments):
            src, dst = _cmd_name(segments[n]), _cmd_name(segments[n + 1])
            if dst in SHELLS and src in DOWNLOADERS:
                risk.bump("dangerous", "downloads code from the internet and runs it immediately")
            elif dst in SHELLS and dst not in ("python", "python3", "node", "perl", "ruby", "php"):
                risk.bump("destructive", "runs piped text as shell commands")

    for words in segments:
        _classify_segment(words, risk, _depth)
    for words in segments:
        if any(SENSITIVE_RE.search(w) for w in words) or (words and words[0] in ("printenv", "env") and len(words) == 1):
            risk.sensitive = True
    return risk


def _cmd_name(words: list[str]) -> str:
    words = _strip_wrappers(words, Risk())
    return words[0].rsplit("/", 1)[-1] if words else ""


def _strip_wrappers(words: list[str], risk: Risk) -> list[str]:
    w = list(words)
    for _ in range(6):
        while w and re.match(r"^[A-Za-z_]\w*=", w[0]):
            w.pop(0)
        if not w:
            break
        name = w[0].rsplit("/", 1)[-1]
        if name not in WRAPPERS:
            break
        w.pop(0)
        if name in ("sudo", "doas"):
            risk.bump("safe", "")
            risk.reasons = [r for r in risk.reasons if r]
            while w and w[0].startswith("-"):
                opt = w.pop(0)
                if opt in ("-u", "-g", "-h", "-p", "-C") and w:
                    w.pop(0)
        elif name == "timeout" and w:
            while w and w[0].startswith("-"):
                w.pop(0)
            if w:
                w.pop(0)
        elif name in ("watch", "nice", "ionice", "xargs", "stdbuf", "env"):
            while w and (w[0].startswith("-") or re.match(r"^[A-Za-z_]\w*=", w[0])):
                opt = w.pop(0)
                if opt in ("-n", "-d", "-I", "-L", "-P") and w:
                    w.pop(0)
        else:
            while w and w[0].startswith("-"):
                w.pop(0)
    return w


def _flags(words: list[str]) -> set[str]:
    out: set[str] = set()
    for w in words[1:]:
        if w.startswith("--"):
            out.add(w.split("=")[0])
        elif w.startswith("-") and len(w) > 1:
            out.update("-" + c for c in w[1:])
    return out


def _classify_segment(words: list[str], risk: Risk, depth: int) -> None:
    uses_sudo = any(x.rsplit("/", 1)[-1] in ("sudo", "doas") for x in words[:3])
    w = _strip_wrappers(words, risk)
    if not w:
        return
    cmd, args = w[0].rsplit("/", 1)[-1], w[1:]
    flags = _flags(w)
    if uses_sudo:
        risk.reasons.append("runs with administrator (root) privileges") if risk.level != "safe" else None

    if cmd in ("sh", "bash", "zsh", "dash") and "-c" in args and depth < 3:
        idx = args.index("-c")
        if idx + 1 < len(args):
            inner = classify(args[idx + 1], depth + 1)
            risk.bump(inner.level, "inner command: " + inner.explanation)
            risk.sensitive = risk.sensitive or inner.sensitive
            return
    if cmd in DANGEROUS_CMDS or cmd.startswith("mkfs"):
        risk.bump("dangerous", DANGEROUS_CMDS.get(cmd, "formats a disk (erases everything on it)"))
        return
    handler = _HANDLERS.get(cmd)
    if handler:
        handler(args, flags, risk)
        return
    if cmd in DESTRUCTIVE_CMDS:
        risk.bump("destructive", DESTRUCTIVE_CMDS[cmd])
    elif cmd in WRITE_CMDS:
        risk.bump("writes", f"{cmd} can create or change files or settings")
    elif cmd in SAFE_CMDS:
        pass
    else:
        risk.bump("writes", f"“{cmd}” isn't a recognised read-only command")


# ------------------------------------------------------------------ per-command handlers
def _rm(args, flags, risk):
    targets = [a for a in args if not a.startswith("-")]
    rec = bool(flags & {"-r", "-R", "--recursive"})
    if "--no-preserve-root" in flags:
        risk.bump("dangerous", "rm with --no-preserve-root can wipe the whole system")
    for t in targets:
        if t in HOME_LIKE or is_protected(t.rstrip("/") or "/", "/nonexistent-home"):
            risk.bump("dangerous", f"would delete {t}, a system or home location")
            break
    risk.bump("destructive", "recursively deletes folders and everything inside" if rec
              else "rm deletes files permanently")


def _find(args, flags, risk):
    if flags & {"-delete"} or "-delete" in args:
        risk.bump("destructive", "find -delete removes every match permanently")
    elif "-exec" in args or "-execdir" in args or "-ok" in args:
        risk.bump("writes", "find -exec runs another command on every match")


def _sed(args, flags, risk):
    if any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args):
        risk.bump("writes", "sed -i edits files in place")


def _chmod(args, flags, risk):
    targets = [a for a in args if not a.startswith("-")][1:]
    if flags & {"-R", "--recursive"} and any(t in HOME_LIKE or is_protected(t, "/x") for t in targets):
        risk.bump("dangerous", "recursively changes permissions/ownership on a system or home folder")
    else:
        risk.bump("writes", "changes permissions or ownership" + (" recursively" if flags & {"-R"} else ""))


def _dd(args, flags, risk):
    if any(a.startswith("of=/dev/") for a in args):
        risk.bump("dangerous", "dd is writing straight onto a device and can erase a disk")
    else:
        risk.bump("destructive", "dd copies raw data and can overwrite files")


def _verbs(table_safe, table_writes, table_destructive, table_dangerous=(), name="it"):
    def h(args, flags, risk):
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in table_dangerous:
            risk.bump("dangerous", f"{name} {verb} can take the server down or lock you out")
        elif verb in table_destructive:
            risk.bump("destructive", f"{name} {verb} stops or removes something")
        elif verb in table_writes:
            risk.bump("writes", f"{name} {verb} changes state")
        elif verb in table_safe or (not verb and name in ("systemctl", "docker", "git")):
            pass
        else:
            risk.bump("writes", f"{name} {verb or ''} isn't a recognised read-only action".replace("  ", " "))
    return h


def _systemctl(args, flags, risk):
    base = _verbs({"status", "is-active", "is-enabled", "is-failed", "show", "cat", "list-units", "list-unit-files",
                   "list-timers", "list-sockets", "list-dependencies", "get-default"},
                  {"start", "restart", "reload", "try-restart", "reload-or-restart", "enable", "daemon-reload",
                   "reset-failed", "set-default"},
                  {"stop", "disable", "mask", "kill"},
                  {"reboot", "poweroff", "halt", "isolate", "suspend", "hibernate", "kexec", "rescue", "emergency"},
                  "systemctl")
    base(args, flags, risk)
    verb = next((a for a in args if not a.startswith("-")), "")
    unit = " ".join(a for a in args if not a.startswith("-"))
    if re.search(r"\bssh", unit) and verb in ("stop", "disable", "mask", "restart", "reload"):
        risk.bump("dangerous", "affects the SSH service, which could lock you out of this server")


def _service(args, flags, risk):
    if len(args) >= 2:
        _systemctl([args[1], args[0]], flags, risk)
    elif args:
        risk.bump("writes", "service action")


_docker = _verbs({"ps", "images", "logs", "inspect", "info", "version", "stats", "top", "port", "ls", "config",
                  "events", "history", "diff", "search", "context", "compose"},
                 {"start", "restart", "pull", "build", "up", "exec", "run", "create", "cp", "commit", "tag", "push",
                  "pause", "unpause", "update", "load", "save", "login"},
                 {"stop", "rm", "rmi", "kill", "prune", "down", "logout"}, (), "docker")


def _docker_h(args, flags, risk):
    rest = [a for a in args if not a.startswith("-")]
    if rest and rest[0] == "compose":
        rest = rest[1:]
        args = rest
    if any(a in ("prune", "rm", "rmi", "kill", "stop", "down") for a in rest):
        risk.bump("destructive", "removes or stops containers/images/volumes")
    elif any(a in ("system", "volume", "network", "image", "container") for a in rest[:1]) and \
            (len(rest) < 2 or rest[1] in ("ls", "inspect", "df", "ps", "list", "history")):
        pass
    else:
        _docker(args, flags, risk)


def _git(args, flags, risk):
    verb = next((a for a in args if not a.startswith("-")), "")
    if verb == "reset" and "--hard" in args or verb == "clean" or (verb == "push" and flags & {"--force", "-f", "--force-with-lease"}) \
            or (verb == "branch" and flags & {"-D"}) or (verb == "checkout" and "--" in args):
        risk.bump("destructive", f"git {verb} can discard work permanently")
    elif verb in ("status", "log", "diff", "show", "branch", "remote", "ls-files", "blame", "describe", "tag",
                  "stash", "config", "rev-parse", "shortlog", "reflog", ""):
        if verb == "stash" and (len(args) < 2 or args[1] != "list"):
            risk.bump("writes", "git stash changes the working tree")
    else:
        risk.bump("writes", f"git {verb} changes the repository or files")


def _pkg(read_verbs, write_verbs, destructive_verbs, name):
    def h(args, flags, risk):
        if "-s" in flags or "--simulate" in flags or "--dry-run" in flags or "--assume-no" in flags:
            return
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in destructive_verbs:
            risk.bump("destructive", f"{name} {verb} removes software")
        elif verb in write_verbs:
            risk.bump("writes", f"{name} {verb} installs or updates software")
        elif verb in read_verbs:
            pass
        else:
            risk.bump("writes", f"{name} {verb} isn't a recognised read-only action")
    return h


_apt = _pkg({"list", "search", "show", "policy", "depends", "rdepends", "madison", "showpkg", "changelog"},
            {"install", "update", "upgrade", "dist-upgrade", "full-upgrade", "reinstall", "clean", "autoclean",
             "download", "source", "build-dep", "edit-sources"},
            {"remove", "purge", "autoremove", "erase"}, "apt")
_dnf = _pkg({"list", "search", "info", "repolist", "provides", "history", "check-update", "deplist"},
            {"install", "update", "upgrade", "reinstall", "clean", "makecache", "downgrade"},
            {"remove", "erase", "autoremove"}, "dnf/yum")


def _pacman(args, flags, risk):
    if flags & {"-R", "-Rs", "-Rns"} or any(a.startswith("-R") for a in args):
        risk.bump("destructive", "pacman -R removes software")
    elif any(a.startswith(("-Q", "-Ss", "-Si", "-F")) for a in args):
        pass
    else:
        risk.bump("writes", "pacman installs or updates software")


def _apk(args, flags, risk):
    _pkg({"info", "list", "search", "policy"}, {"add", "update", "upgrade", "fix"}, {"del"}, "apk")(args, flags, risk)


def _dpkg(args, flags, risk):
    if flags & {"-l", "-s", "-L", "-S", "-p", "--list", "--status", "--listfiles", "--search"}:
        return
    if flags & {"-r", "-P", "--remove", "--purge"}:
        risk.bump("destructive", "dpkg removes software")
    else:
        risk.bump("writes", "dpkg installs or configures software")


def _rpm(args, flags, risk):
    if any(a.startswith("-q") for a in args) or "--query" in flags:
        return
    risk.bump("destructive" if flags & {"-e", "--erase"} else "writes", "rpm changes installed software")


def _crontab(args, flags, risk):
    if "-l" in args:
        return
    risk.bump("destructive" if "-r" in args else "writes",
              "crontab -r deletes all scheduled jobs" if "-r" in args else "changes scheduled jobs")


def _curl(args, flags, risk):
    if flags & {"-o", "-O", "-T", "-d", "-F", "--output", "--data", "--data-raw", "--data-binary", "--upload-file", "--form"} \
            or any(a == "-X" and i + 1 < len(args) and args[i + 1].upper() != "GET" for i, a in enumerate(args)):
        risk.bump("writes", "curl is saving a file or sending data")


def _wget(args, flags, risk):
    if "--spider" in flags or "-O-" in args or ("-O" in args and "-" in args):
        return
    risk.bump("writes", "wget saves a file to disk")


def _journalctl(args, flags, risk):
    if any(a.startswith(("--vacuum", "--rotate", "--flush")) for a in args):
        risk.bump("destructive", "journalctl is deleting old log data")


def _ip(args, flags, risk):
    verbs = [a for a in args if not a.startswith("-")]
    if len(verbs) >= 2 and verbs[1] in ("add", "del", "delete", "flush", "set", "replace", "change", "down"):
        risk.bump("dangerous", "changes network configuration and could cut off your connection")


def _mount(args, flags, risk):
    if [a for a in args if not a.startswith("-")]:
        risk.bump("writes", "mounts a filesystem")


def _ufw(args, flags, risk):
    verb = next((a for a in args if not a.startswith("-")), "")
    if verb in ("status", "show", "version", "app"):
        return
    risk.bump("dangerous", "changes firewall rules; a wrong rule can lock you out of SSH")


def _tee(args, flags, risk):
    risk.bump("writes", "tee writes its input to files")


def _awk(args, flags, risk):
    if any("system(" in a for a in args):
        risk.bump("writes", "awk program runs other commands")


def _mysql(args, flags, risk):
    text = " ".join(args).lower()
    if re.search(r"\b(drop|truncate)\s+(table|database|schema)\b|\bdelete\s+from\b", text):
        risk.bump("destructive", "SQL that deletes data")
    elif re.search(r"\b(insert|update|alter|create|grant)\b", text):
        risk.bump("writes", "SQL that changes data")


_HANDLERS = {
    "rm": _rm, "find": _find, "sed": _sed, "chmod": _chmod, "chown": _chmod, "chgrp": _chmod, "dd": _dd,
    "systemctl": _systemctl, "service": _service, "docker": _docker_h, "docker-compose": _docker_h, "git": _git,
    "apt": _apt, "apt-get": _apt, "aptitude": _apt, "apt-cache": lambda a, f, r: None, "dnf": _dnf, "yum": _dnf,
    "zypper": _dnf, "pacman": _pacman, "apk": _apk, "dpkg": _dpkg, "rpm": _rpm, "crontab": _crontab,
    "curl": _curl, "wget": _wget, "journalctl": _journalctl, "ip": _ip, "mount": _mount, "ufw": _ufw,
    "tee": _tee, "awk": _awk, "gawk": _awk, "mysql": _mysql, "psql": _mysql, "mariadb": _mysql,
}
