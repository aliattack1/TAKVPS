# TAKVPS
**v1.0.0** — Client-only SSH/VPS manager (Flet + asyncssh). No backend; nothing installed on servers.

Manage your Linux servers from your phone or desktop. Connect via SSH, browse files, run commands,
monitor resources, manage Docker/Nginx/WireGuard/Kubernetes, chat with an AI assistant, and more —
all from one app, with nothing installed on the server.

## Features

- **SSH terminal** — full VT100/xterm emulator with wide-char (CJK/emoji) support, mouse reporting,
  function keys (F1-F12), command history, search-in-scrollback, copy/select, and tmux session persistence.
- **File manager** — SFTP browsing, drag-and-drop uploads (desktop), folder download/upload, recycle bin,
  bookmarks, transfer queue with progress bars, and auto-refresh after uploads.
- **Code editor** — multi-tab editing with syntax highlighting (12+ languages), find/replace, save with
  permission preservation, and optional backup before overwrite.
- **Monitoring** — live CPU/RAM/disk/network stats with real line charts, per-core CPU, top processes,
  health score, and configurable thresholds.
- **AI assistant** — bring-your-own-key (OpenAI, Anthropic, Gemini, local Ollama/LM Studio). Native
  tool-calling, per-server system prompts, conversation branching, and command risk classification.
- **Server managers** — Docker, Nginx, Caddy/Traefik, systemd services, cron, packages, firewall,
  processes, network, users & SSH keys, databases, WireGuard VPN, Kubernetes.
- **Fleet operations** — run commands across all saved servers in parallel with live progress.
- **Security** — encrypted credential vault, audit log with tamper-evidence, app lock (PIN), and
  per-server AI safety constraints.
- **Backup & restore** — encrypted full-app-state export bundle for easy migration.

## Run (desktop)
    pip install -r requirements.txt
    flet run main.py

## Test
    pytest

## Build
    flet build apk        # Android (arm64-v8a)
    flet build windows    # Windows
    flet build linux      # Linux
    flet build macos      # macOS

`cryptography` (asyncssh's only native dependency) is resolved from pypi.flet.dev.

## CI
GitHub Actions workflows in `.github/workflows/`:
- `tests.yml` — runs pytest on every push/PR (Python 3.10, 3.11, 3.12).
- `release.yml` — on a `v*` tag push, builds Android APK + Windows .exe + Linux tar.gz + macOS .app
  and creates a GitHub Release with all artifacts attached.
