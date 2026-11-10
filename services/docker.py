"""Docker: detection, safe command builders and JSON parsers. Pure, no UI."""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

_REF = re.compile(r"^[A-Za-z0-9][\w.-]*$")
_IMAGE = re.compile(r"^[A-Za-z0-9][\w./:@-]*$")

DETECT_CMD = ("if command -v docker >/dev/null 2>&1; then echo '##INSTALLED'; docker --version 2>&1; "
              "echo '##INFO'; docker info --format '{{.ServerVersion}}' 2>&1 | head -n 3; "
              "echo '##COMPOSE'; docker compose version 2>&1 | head -n 1; else echo '##MISSING'; fi")
PS_CMD = "docker ps -a --format '{{json .}}'"
IMAGES_CMD = "docker images --format '{{json .}}'"
VOLUMES_CMD = "docker volume ls --format '{{json .}}'"
NETWORKS_CMD = "docker network ls --format '{{json .}}'"
STATS_CMD = "docker stats --no-stream --format '{{json .}}'"
COMPOSE_LS_CMD = "docker compose ls -a --format json"
BUILTIN_NETWORKS = {"bridge", "host", "none"}


@dataclass
class DockerState:
    installed: bool = False
    version: str = ""
    daemon: str = "unknown"       # running | stopped | denied | unknown
    server_version: str = ""
    compose: bool = False


def parse_detect(text: str) -> DockerState:
    if "##MISSING" in text or "##INSTALLED" not in text:
        return DockerState()
    sec: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("##"):
            cur = ln[2:]
            sec[cur] = []
        elif cur:
            sec[cur].append(ln.strip())
    st = DockerState(installed=True, version=(sec.get("INSTALLED") or [""])[0])
    info = " ".join(sec.get("INFO", []))
    low = info.lower()
    if "permission denied" in low:
        st.daemon = "denied"
    elif "cannot connect" in low or "is the docker daemon running" in low or "error during connect" in low:
        st.daemon = "stopped"
    elif re.match(r"^\d", info.strip()):
        st.daemon, st.server_version = "running", info.strip().split()[0]
    st.compose = any(l.lower().startswith("docker compose version") for l in sec.get("COMPOSE", []))
    return st


def parse_json_lines(text: str) -> list[dict]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


@dataclass
class Container:
    id: str
    name: str
    image: str
    state: str
    status: str
    ports: str = ""


def parse_containers(text: str) -> list[Container]:
    out = [Container(d.get("ID", ""), d.get("Names", ""), d.get("Image", ""), d.get("State", ""),
                     d.get("Status", ""), d.get("Ports", "")) for d in parse_json_lines(text)]
    return sorted(out, key=lambda c: (c.state != "running", c.name.lower()))


@dataclass
class Image:
    id: str
    repo: str
    tag: str
    size: str
    created: str

    @property
    def ref(self) -> str:
        return f"{self.repo}:{self.tag}" if self.repo not in ("", "<none>") and self.tag != "<none>" else self.id


def parse_images(text: str) -> list[Image]:
    return sorted((Image(d.get("ID", ""), d.get("Repository", ""), d.get("Tag", ""), d.get("Size", ""),
                         d.get("CreatedSince", "")) for d in parse_json_lines(text)), key=lambda i: i.ref.lower())


def parse_volumes(text: str) -> list[dict]:
    return sorted(({"name": d.get("Name", ""), "driver": d.get("Driver", "")} for d in parse_json_lines(text)),
                  key=lambda v: v["name"])


def parse_networks(text: str) -> list[dict]:
    return sorted(({"id": d.get("ID", ""), "name": d.get("Name", ""), "driver": d.get("Driver", ""),
                    "scope": d.get("Scope", "")} for d in parse_json_lines(text)), key=lambda n: n["name"])


def parse_stats(text: str) -> dict[str, dict]:
    return {d.get("Name", ""): {"cpu": d.get("CPUPerc", ""), "mem": d.get("MemUsage", ""),
                                "memp": d.get("MemPerc", ""), "net": d.get("NetIO", ""), "blk": d.get("BlockIO", "")}
            for d in parse_json_lines(text)}


@dataclass
class ComposeProject:
    name: str
    status: str
    files: list[str] = field(default_factory=list)


def parse_compose_ls(text: str) -> list[ComposeProject]:
    t = text.strip()
    items: list[dict] = []
    try:
        data = json.loads(t) if t.startswith("[") else None
        items = data if isinstance(data, list) else parse_json_lines(t)
    except ValueError:
        items = parse_json_lines(t)
    return [ComposeProject(d.get("Name", ""), d.get("Status", ""),
                           [f for f in str(d.get("ConfigFiles", "")).split(",") if f])
            for d in items if isinstance(d, dict) and d.get("Name")]


# ------------------------------------------------------------------ command builders
def valid_ref(r: str) -> bool:
    return bool(_REF.match(r)) and len(r) < 200


def valid_image(r: str) -> bool:
    return bool(_IMAGE.match(r)) and len(r) < 300


def _ref(r: str) -> str:
    if not valid_ref(r):
        raise ValueError("Invalid container name or id.")
    return shlex.quote(r)


CONTAINER_VERBS = {"start": "start", "stop": "stop", "restart": "restart", "pause": "pause", "unpause": "unpause",
                   "kill": "kill", "rm": "rm", "rmf": "rm -f"}


def container_command(verb: str, ref: str) -> str:
    if verb not in CONTAINER_VERBS:
        raise ValueError("Unknown action.")
    return f"docker {CONTAINER_VERBS[verb]} {_ref(ref)}"


def logs_command(ref: str, tail: int = 200, follow: bool = False) -> str:
    return f"docker logs --tail {max(1, min(int(tail), 5000))}{' -f' if follow else ''} {_ref(ref)} 2>&1"


def inspect_command(ref: str) -> str:
    return f"docker inspect {_ref(ref)}"


def exec_command(ref: str) -> str:
    return f"docker exec -it {_ref(ref)} sh -c 'command -v bash >/dev/null 2>&1 && exec bash || exec sh'"


def pull_command(image: str) -> str:
    if not valid_image(image):
        raise ValueError("Invalid image name.")
    return f"docker pull {shlex.quote(image)}"


def image_rm_command(ref: str) -> str:
    if not valid_image(ref):
        raise ValueError("Invalid image reference.")
    return f"docker rmi {shlex.quote(ref)}"


def volume_rm_command(name: str) -> str:
    return f"docker volume rm {_ref(name)}"


def network_rm_command(name: str) -> str:
    return f"docker network rm {_ref(name)}"


PRUNES = {"containers": "docker container prune -f", "images": "docker image prune -f",
          "images_all": "docker image prune -af", "volumes": "docker volume prune -f",
          "everything": "docker system prune -af"}
COMPOSE_VERBS = {"up": "up -d", "down": "down", "ps": "ps", "logs": "logs --tail 200", "pull": "pull",
                 "restart": "restart"}


def compose_command(p: ComposeProject, verb: str) -> str:
    if verb not in COMPOSE_VERBS:
        raise ValueError("Unknown action.")
    if not valid_ref(p.name) or not p.files or not all(f.startswith("/") for f in p.files):
        raise ValueError("This compose project has no usable config file path.")
    files = " ".join(f"-f {shlex.quote(f)}" for f in p.files)
    return f"docker compose {files} -p {shlex.quote(p.name)} {COMPOSE_VERBS[verb]}"


def pretty_inspect(text: str, limit: int = 12000) -> str:
    try:
        s = json.dumps(json.loads(text), indent=2)
    except ValueError:
        s = text
    return s if len(s) <= limit else s[:limit] + "\n… (truncated)"
