"""Kubernetes manager.

Lists pods, deployments, services; view logs; exec into a pod. Uses the
server's own `kubectl` — nothing is installed by the app.

Pure: no Flet, no asyncssh. Fully testable.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class K8sPod:
    name: str
    ready: str = ""
    status: str = ""
    restarts: int = 0
    node: str = ""
    age: str = ""
    namespace: str = "default"


@dataclass
class K8sDeployment:
    name: str
    ready: str = ""
    up_to_date: int = 0
    available: int = 0
    age: str = ""
    namespace: str = "default"


@dataclass
class K8sService:
    name: str
    type: str = ""
    cluster_ip: str = ""
    external_ip: str = ""
    ports: str = ""
    age: str = ""
    namespace: str = "default"


def detect_command() -> str:
    """Check if kubectl is installed and connected to a cluster."""
    return "kubectl version --client 2>/dev/null && kubectl get ns default 2>/dev/null"


def list_pods_command(namespace: str = "") -> str:
    ns = f"-n {shlex.quote(namespace)}" if namespace else "--all-namespaces"
    return f"kubectl get pods {ns} -o json 2>/dev/null"


def list_deployments_command(namespace: str = "") -> str:
    ns = f"-n {shlex.quote(namespace)}" if namespace else "--all-namespaces"
    return f"kubectl get deployments {ns} -o json 2>/dev/null"


def list_services_command(namespace: str = "") -> str:
    ns = f"-n {shlex.quote(namespace)}" if namespace else "--all-namespaces"
    return f"kubectl get svc {ns} -o json 2>/dev/null"


def logs_command(pod: str, namespace: str = "default", follow: bool = False, tail: int = 100) -> str:
    """Get logs for a pod. If `follow`, stream them."""
    f = "-f" if follow else ""
    return f"kubectl logs {f} --tail={tail} -n {shlex.quote(namespace)} {shlex.quote(pod)}"


def exec_command(pod: str, namespace: str = "default", cmd: str = "/bin/sh") -> str:
    """Exec into a pod."""
    return f"kubectl exec -it -n {shlex.quote(namespace)} {shlex.quote(pod)} -- {cmd}"


def parse_pods(json_text: str) -> list[K8sPod]:
    """Parse `kubectl get pods -o json` output."""
    try:
        data = json.loads(json_text)
    except ValueError:
        return []
    pods: list[K8sPod] = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})
        spec = item.get("spec", {})
        ns = meta.get("namespace", "default")
        name = meta.get("name", "")
        container_statuses = status.get("containerStatuses", [])
        ready = sum(1 for c in container_statuses if c.get("ready"))
        total = len(container_statuses)
        phase = status.get("phase", "Unknown")
        restarts = sum(c.get("restartCount", 0) for c in container_statuses)
        pods.append(K8sPod(
            name=name, namespace=ns, ready=f"{ready}/{total}",
            status=phase, restarts=restarts,
            node=spec.get("nodeName", ""),
        ))
    return pods


def parse_deployments(json_text: str) -> list[K8sDeployment]:
    """Parse `kubectl get deployments -o json` output."""
    try:
        data = json.loads(json_text)
    except ValueError:
        return []
    deps: list[K8sDeployment] = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        deps.append(K8sDeployment(
            name=meta.get("name", ""),
            namespace=meta.get("namespace", "default"),
            ready=f"{status.get('readyReplicas', 0)}/{spec.get('replicas', 0)}",
            up_to_date=status.get("updatedReplicas", 0),
            available=status.get("availableReplicas", 0),
        ))
    return deps


def parse_services(json_text: str) -> list[K8sService]:
    """Parse `kubectl get svc -o json` output."""
    try:
        data = json.loads(json_text)
    except ValueError:
        return []
    svcs: list[K8sService] = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        ports = ", ".join(
            f"{p.get('port')}/{p.get('protocol', 'TCP')}"
            for p in spec.get("ports", [])
        )
        svcs.append(K8sService(
            name=meta.get("name", ""),
            namespace=meta.get("namespace", "default"),
            type=spec.get("type", "ClusterIP"),
            cluster_ip=spec.get("clusterIP", ""),
            external_ip=", ".join(spec.get("externalIPs", [])) or "<none>",
            ports=ports,
        ))
    return svcs


def classify_kubectl_command(cmd: str) -> str:
    """Risk-classify a kubectl subcommand (mirrors the risk classifier)."""
    cmd = cmd.strip()
    if cmd.startswith("kubectl get") or cmd.startswith("kubectl describe"):
        return "safe"
    if cmd.startswith("kubectl logs") or cmd.startswith("kubectl top"):
        return "safe"
    if cmd.startswith("kubectl delete"):
        return "destructive"
    if cmd.startswith("kubectl apply") or cmd.startswith("kubectl create"):
        return "writes"
    return "writes"
