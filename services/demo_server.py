"""Phase 15 (G3) — Demo server stub.

Provides a read-only fake connection profile so users can explore the app
before connecting a real server. The demo "server" returns canned data for
sysinfo, monitor, and file listings — no SSH connection is made.

This is a stub for testing the UI, not a real server. The data is static.
"""
from __future__ import annotations

DEMO_PROFILE = {
    "id": "demo-server",
    "name": "Demo Server",
    "host": "demo.takvps.local",
    "username": "demo",
    "port": 22,
    "auth": "password",
    "tags": ["demo"],
    "color": "#22C55E",
    "ai_context": "",
}

DEMO_SYSINFO = {
    "distro": "Ubuntu 22.04.3 LTS",
    "kernel": "5.15.0-91-generic",
    "arch": "x86_64",
    "package_manager": "apt",
    "sudo": "passwordless",
}

DEMO_MONITOR = {
    "cpu": 23.5,
    "cores": [15.0, 28.0, 19.0, 32.0],
    "mem_pct": 42.0,
    "mem_total_kb": 4_096_000,
    "mem_used_kb": 1_720_320,
    "swap_pct": 0.0,
    "swap_total_kb": 0,
    "load": [0.92, 0.85, 0.78],
    "uptime_s": 86400 * 12 + 3600 * 3,
    "procs": 142,
    "users": 1,
    "temp_c": 52.0,
    "disk_read": 1_200_000,
    "disk_write": 800_000,
    "net_down": 450_000,
    "net_up": 120_000,
    "mounts": [
        {"mount": "/", "size_kb": 20_000_000, "used_kb": 8_500_000, "pct": 42},
        {"mount": "/home", "size_kb": 50_000_000, "used_kb": 12_000_000, "pct": 24},
    ],
}

DEMO_FILES = [
    {"name": ".bashrc", "size": 3771, "is_dir": False, "perms": 0o644, "owner": "demo", "group": "demo"},
    {"name": ".profile", "size": 807, "is_dir": False, "perms": 0o644, "owner": "demo", "group": "demo"},
    {"name": ".ssh", "size": 0, "is_dir": True, "perms": 0o700, "owner": "demo", "group": "demo"},
    {"name": "projects", "size": 0, "is_dir": True, "perms": 0o755, "owner": "demo", "group": "demo"},
    {"name": "deploy.sh", "size": 1024, "is_dir": False, "perms": 0o755, "owner": "demo", "group": "demo"},
    {"name": "README.md", "size": 512, "is_dir": False, "perms": 0o644, "owner": "demo", "group": "demo"},
]


def is_demo_profile(profile_id: str) -> bool:
    """True if this is the demo server."""
    return profile_id == DEMO_PROFILE["id"]
