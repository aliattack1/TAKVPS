"""App settings stored as JSON in the app's private data dir."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_NAV = ["overview", "monitor", "files", "tools", "terminal", "editor"]
# 'tools' is the hub for every manager (Docker, Nginx, Cron, ...).
# Without it in the bottom bar, the only way to reach the hub on a phone was the
# search palette — that was the bug. Migration in ui/shell.py adds it to existing
# installations at index 3 (4th slot) so the bottom bar shows overview/monitor/
# files/tools + More.


def data_dir() -> Path:
    base = os.environ.get("FLET_APP_STORAGE_DATA") or str(Path.home() / ".takvps")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Settings:
    theme_mode: str = "system"          # light | dark | system
    language: str = "fa"               # fa (Persian) is the default; user can switch to en in Settings
    preset: str = "default"
    accent: str = "#3B82F6"
    font_scale: float = 1.0
    monitor_interval: float = 2.0
    nav_order: list[str] = field(default_factory=lambda: list(DEFAULT_NAV))
    safe_mode: bool = False
    last_connection_id: str = ""
    connect_on_launch: bool = False
    first_run_done: bool = False      # first-run tour shown?
    ai_active_profile: str = ""
    ai_default_mode: str = "suggest"
    ai_step_limit: int = 8
    ai_native_tools: bool = True    # use native tool-calling when the provider supports it
    ai_show_tool_calls: bool = False  # show raw tool-call payloads in the chat
    ai_prefer_local: bool = False     # prefer local model when on same network
    fleet_concurrency: int = 4        # max parallel SSH connections for fleet ops
    ai_context: dict = field(default_factory=lambda: {
        "os": True, "shell": True, "cwd": True, "pkg": True, "term": 0, "services": False})
    env_targets: dict = field(default_factory=dict)       # .env path -> service/container to restart
    alerts_enabled: bool = False
    alert_interval: int = 60
    alert_services: list[str] = field(default_factory=list)
    editor_font_size: int = 14
    terminal_font_size: int = 12
    terminal_auto_scroll: bool = True   # always scroll terminal to bottom on new output
    editor_wrap: bool = True
    editor_backup: bool = False
    # real LineChart in the Monitor. Falls back to bar sparkline when
    # flet-charts isn't installed (see services/charts.charts_available).
    monitor_line_charts: bool = True
    files_sort: str = "name"
    files_reverse: bool = False
    files_hidden: bool = False
    file_bookmarks: list[str] = field(default_factory=list)   # starred folder paths
    download_dir: str = ""
    thr_cpu: list[int] = field(default_factory=lambda: [70, 90])
    thr_ram: list[int] = field(default_factory=lambda: [80, 92])
    thr_disk: list[int] = field(default_factory=lambda: [80, 92])
    known_hosts: dict[str, str] = field(default_factory=dict)


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "settings.json"
        self.data = self._load()

    def _load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            base = asdict(Settings())
            base.update({k: v for k, v in raw.items() if k in base})
            return Settings(**base)
        except (OSError, ValueError):
            return Settings()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.data), indent=2), "utf-8")
        tmp.replace(self.path)


def default_download_dir() -> Path:
    """Desktop: ~/Downloads. Packaged/mobile: app-private storage (see README: Android).

    On Android, the app-private storage is NOT visible in the Files app —
    users must use 'Save a copy' in the Transfers dialog to copy to a visible
    location via the system file picker.
    """
    if os.environ.get("FLET_APP_STORAGE_DATA"):
        return data_dir() / "downloads"
    dl = Path.home() / "Downloads"
    return dl if dl.is_dir() else data_dir() / "downloads"


def resolve_download_dir(s: "Settings") -> Path:
    p = Path(s.download_dir) if s.download_dir else default_download_dir()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        p = data_dir() / "downloads"
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Last resort: use a temp directory
            import tempfile
            p = Path(tempfile.gettempdir()) / "takvps-downloads"
            p.mkdir(parents=True, exist_ok=True)
    return p
