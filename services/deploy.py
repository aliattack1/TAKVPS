"""Git deploy recipes: validation and step planning. Pure, no UI."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from services.runner import Step
from services.snippets import cd_target, new_id

RESTART_KINDS = {"none": "No restart", "systemd": "systemd service", "docker": "Docker container",
                 "compose": "docker compose up -d --build", "command": "Custom command"}
_BRANCH = re.compile(r"^[\w./-]+$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass
class Deploy:
    name: str
    project_dir: str
    branch: str = ""
    steps: list[str] = field(default_factory=list)       # build / install commands, one per entry
    restart_kind: str = "none"
    restart_target: str = ""                              # unit / container / command
    health: str = ""                                      # http(s) URL or a command that must exit 0
    scope: str = ""
    id: str = field(default_factory=new_id)


def validate(d: Deploy) -> Optional[str]:
    if not d.name.strip():
        return "Please enter a name."
    if not d.project_dir.strip() or not (d.project_dir.startswith("/") or d.project_dir.startswith("~")):
        return "Project folder must be an absolute path (or start with ~)."
    if d.branch and not _BRANCH.match(d.branch):
        return "Branch names may only contain letters, digits, . _ / -"
    if d.restart_kind not in RESTART_KINDS:
        return "Unknown restart type."
    if d.restart_kind in ("systemd", "docker", "command") and not d.restart_target.strip():
        return "Enter what to restart."
    return None


def is_sha(s: str) -> bool:
    return bool(_SHA.match(s.strip()))


def health_command(health: str) -> str:
    h = health.strip()
    if h.startswith(("http://", "https://")):
        u = shlex.quote(h)
        return ('for i in 1 2 3 4 5; do code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 ' + u + '); '
                'case "$code" in 2*|3*) echo "healthy (HTTP $code)"; exit 0;; esac; '
                'echo "attempt $i: HTTP $code"; sleep 3; done; exit 1')
    return h


def _tail_steps(d: Deploy) -> list[Step]:
    cd = f"cd {cd_target(d.project_dir)} && "
    out = [Step(f"Build: {line.strip()[:40]}", cd + line.strip()) for line in d.steps if line.strip()]
    t = shlex.quote(d.restart_target.strip())
    if d.restart_kind == "systemd":
        out.append(Step("Restart service", f"systemctl restart {t}", sudo=True))
    elif d.restart_kind == "docker":
        out.append(Step("Restart container", f"docker restart {t}"))
    elif d.restart_kind == "compose":
        out.append(Step("docker compose up", cd + "docker compose up -d --build"))
    elif d.restart_kind == "command":
        out.append(Step("Restart", cd + d.restart_target.strip()))
    if d.health.strip():
        out.append(Step("Health check", health_command(d.health)))
    return out


def plan_deploy(d: Deploy) -> list[Step]:
    err = validate(d)
    if err:
        raise ValueError(err)
    cd = f"cd {cd_target(d.project_dir)} && "
    pull = "git fetch --all --prune"
    if d.branch:
        pull += f" && git checkout {shlex.quote(d.branch)}"
    pull += " && git pull --ff-only"
    return [Step("Pull latest code", cd + pull)] + _tail_steps(d)


def plan_rollback(d: Deploy, commit: str) -> list[Step]:
    if not is_sha(commit):
        raise ValueError("Invalid commit id.")
    cd = f"cd {cd_target(d.project_dir)} && "
    return [Step(f"Reset to {commit[:8]}", cd + f"git reset --hard {commit}")] + _tail_steps(d)


def all_commands(d: Deploy) -> list[str]:
    """Every command a deploy would run (for risk checks)."""
    try:
        return [s.cmd for s in plan_deploy(d)]
    except ValueError:
        return []
