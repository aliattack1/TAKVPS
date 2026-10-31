"""Snippet packs: export/import JSON with a risk check of every command. Pure."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from services import deploy as D
from services import snippets as S
from services.risk import LEVELS, Risk, classify

FORMAT = "takvps-pack"
MAX_ITEMS = 200


class PackError(ValueError):
    pass


@dataclass
class Pack:
    name: str
    snippets: list[S.Snippet] = field(default_factory=list)
    deploys: list[D.Deploy] = field(default_factory=list)


_SN_KEYS = ("name", "command", "cwd", "sudo", "background", "group", "icon", "color")
_DP_KEYS = ("name", "project_dir", "branch", "steps", "restart_kind", "restart_target", "health")


def export_pack(name: str, snippets: list[S.Snippet], deploys: list[D.Deploy]) -> str:
    return json.dumps({
        "format": FORMAT, "version": 1, "name": name,
        "snippets": [{k: asdict(s)[k] for k in _SN_KEYS} for s in snippets],
        "deploys": [{k: asdict(d)[k] for k in _DP_KEYS} for d in deploys]}, indent=2)


def parse_pack(text: str) -> Pack:
    try:
        raw = json.loads(text.strip())
    except ValueError as e:
        raise PackError("That isn't valid pack text.") from e
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise PackError("That isn't a TAKVPS pack.")
    sns, dps = raw.get("snippets", []), raw.get("deploys", [])
    if not isinstance(sns, list) or not isinstance(dps, list) or len(sns) + len(dps) > MAX_ITEMS:
        raise PackError("The pack is malformed or too large.")
    pack = Pack(name=str(raw.get("name", "Imported pack"))[:80])
    try:
        for d in sns:
            s = S.Snippet(**{k: d[k] for k in _SN_KEYS if k in d})
            s.sudo, s.background = bool(s.sudo), bool(s.background)
            if (err := S.validate(s)):
                raise PackError(f"Snippet “{s.name or '?'}”: {err}")
            pack.snippets.append(s)
        for d in dps:
            dp = D.Deploy(**{k: d[k] for k in _DP_KEYS if k in d})
            dp.steps = [str(x) for x in (dp.steps or [])]
            if (err := D.validate(dp)):
                raise PackError(f"Deploy “{dp.name or '?'}”: {err}")
            pack.deploys.append(dp)
    except (TypeError, KeyError, AttributeError) as e:
        raise PackError("The pack contains an invalid entry.") from e
    return pack


def worst_risk(commands: list[str]) -> Risk:
    worst = Risk()
    for c in commands:
        r = classify(c)
        if LEVELS[r.level] > LEVELS[worst.level] or (r.level == worst.level and r.sensitive):
            worst = r
    return worst


def snippet_risk(s: S.Snippet) -> Risk:
    return classify(s.command)


def deploy_risk(d: D.Deploy) -> Risk:
    return worst_risk(D.all_commands(d))


def _sn(name, command, group, icon="TERMINAL", sudo=False, **kw) -> dict:
    return {"name": name, "command": command, "group": group, "icon": icon, "sudo": sudo, **kw}


BUILTIN_PACKS: dict[str, dict] = {
    "Fresh server basics": {"snippets": [
        _sn("System summary", "uname -a; echo; cat /etc/os-release | head -n 4; echo; uptime", "Basics", "SPEED"),
        _sn("Disk & memory", "df -h; echo; free -h", "Basics", "STORAGE"),
        _sn("Listening ports", "ss -tulpn", "Basics", "SECURITY", sudo=True),
        _sn("Failed services", "systemctl --failed --no-pager", "Basics", "BUILD"),
        _sn("Recent logins", "last -n 15", "Basics", "SECURITY"),
        _sn("Update packages (apt)", "apt-get update && apt-get upgrade -y", "Maintenance", "UPDATE", sudo=True),
        _sn("Reboot required?", "[ -f /var/run/reboot-required ] && echo 'Reboot required' || echo 'No reboot needed'",
            "Maintenance", "RESTART_ALT")]},
    "Docker cleanup": {"snippets": [
        _sn("Docker disk usage", "docker system df", "Docker", "STORAGE"),
        _sn("List containers", "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}'", "Docker", "CLOUD"),
        _sn("Remove stopped containers", "docker container prune -f", "Docker", "DELETE_SWEEP"),
        _sn("Remove dangling images", "docker image prune -f", "Docker", "DELETE_SWEEP"),
        _sn("Prune everything unused", "docker system prune -af", "Docker", "DELETE_SWEEP")]},
}


def builtin(name: str) -> Pack:
    d = BUILTIN_PACKS[name]
    return parse_pack(json.dumps({"format": FORMAT, "name": name, "snippets": d.get("snippets", []),
                                  "deploys": d.get("deploys", [])}))
