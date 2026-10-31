"""(G2) — Per-page help text.

A JSON-like dict mapping page keys to short help strings. The shell's topbar
shows a "?" icon that opens a dialog with the current page's help.
"""

PAGE_HELP = {
    "overview": "Server health at a glance: OS, kernel, uptime, load, memory, privilege level, "
    "package manager. Tap any quick-action to run it.",
    "monitor": "Live server stats: CPU (overall + per-core), RAM, swap, disk, network, temperature. "
    "Polls every N seconds (Settings → Monitoring). History is 120 samples in memory. "
    "Health colors: green < amber < red, based on your thresholds.",
    "files": "SFTP file browser: navigate, upload, download, rename, delete, permissions, properties. "
    "Drag files from your OS to upload. Folder download/upload supported. "
    "Delete goes to the Recycle Bin (~/.takvps-recycle/) — restore from there.",
    "editor": "Multi-tab code editor with syntax highlighting (12+ languages). "
    "Save keeps permissions and warns if the file changed on the server. "
    "Find/replace with regex. Adjustable font size.",
    "terminal": "Interactive PTY shell with VT100/xterm emulator. Supports vim/nano/less/htop. "
    "CJK/emoji wide chars, mouse reporting, search-in-scrollback (🔍), tmux sessions, "
    "long-press → Ask AI. Smart auto-scroll stays at bottom unless you scroll up.",
    "snippets": "Reusable commands with {{variables}}, Git deploy recipes, command packs. "
    "Variables are shell-quoted. Risk gate on every run. Background runs use nohup.",
    "services": "systemd service manager: list, start/stop/restart, enable/disable, view logs. "
    "Edit unit files via the editor (saved with sudo install + daemon-reload).",
    "cron": "Cron job manager: visual schedule builder, next-run preview, enable/disable. "
    "Your crontab is validated by the server's `crontab` command. Backups kept (last 10).",
    "packages": "Package manager (apt/dnf/yum/pacman/apk/zypper): update, install, remove. "
    "Security vs normal split. Changelog preview on apt. Reboot-required flag.",
    "docker": "Docker manager: containers, images, volumes, networks, compose. "
    "Live logs, inspect, shell-in-terminal, stats, prune. Auto-detects installation.",
    "nginx": "Nginx manager: sites, config test, reload/restart, HTTPS via certbot. "
    "Templates: static site, reverse proxy, PHP-FPM. Config edit tests after save.",
    "logs": "Log viewer: journald presets + /var/log files. Live follow, filter (text/regex/case), "
    "severity coloring, pause, copy/export.",
    "firewall": "Firewall manager: ufw / firewalld (iptables/nftables read-only). "
    "SSH lockout guard: can't delete the rule that allows your connection port.",
    "processes": "Process manager: signals (TERM/KILL/HUP/INT/STOP/CONT), renice, search/sort. "
    "PID 1 and sshd need typed confirmation. Auto-refresh stops when you leave.",
    "network": "Network: listening ports (public vs local), connections by remote IP, "
    "ping/traceroute/DNS, public-IP lookup (done by the server).",
    "users": "Users & SSH keys: authorized_keys viewer with SHA256 fingerprints, add/remove keys, "
    "failed-login analysis. Removing your last key needs typed confirmation.",
    "quick": "Quick actions: update, clear caches, free memory, vacuum journal, reboot, shutdown. "
    "All confirm; power actions need typed confirmation.",
    "activity": "Audit log of state-changing commands (reads aren't logged). "
    "Each record is SHA-256 chained (tamper-evident). Verify integrity with the ✓ button.",
    "fleet": "All-servers dashboard: parallel health probes + run commands across every server. "
    "▶ button runs a one-off command or saved snippet in parallel. Stop-all cancels everything.",
    "backups": "tar -czf backups of server folders. Excludes, size/free-space check, optional "
    "download to phone, optional daily cron job with delete-after-N-days.",
    "alerts": "Alerts: CRITICAL thresholds (CPU/RAM/disk) + watched services. "
    "Debounced (2 checks in a row, 30-min cooldown). In-app notifications; OS notifications via hook.",
    "health": "Health score: 0–100, computed from one read-only command + update list. "
    "Each penalty explains itself and taps through to the page that fixes it.",
    "diskusage": "Disk usage drill-down with bars + cleanup assistant: package caches, journal, "
    "rotated logs, unused Docker data, unused packages/old kernels. Shows space freed.",
    "databases": "Databases: MySQL/MariaDB, PostgreSQL, SQLite, Redis via the server's CLI. "
    "SQL classified: reads run, writes ask, DROP/TRUNCATE without WHERE need typed RUN. "
    "Dumps are gzip; restore needs typed RESTORE.",
    "tunnels": "SSH tunnels: local forwards bound to 127.0.0.1 only. Stop when connection drops. "
    "Favorites per server.",
    "envedit": ".env editor: parses/edits only the lines you touch. Masks secret-looking keys. "
    "Never logs or sends values. Optional service/container restart after saving.",
    "wireguard": "WireGuard VPN manager: view interface status (running/stopped), start/stop "
    "with wg-quick, view peers and their allowed IPs. Config read from /etc/wireguard/wg0.conf.",
    "k8s": "Kubernetes manager: list pods, deployments, services. View pod logs, exec into a pod. "
    "Uses the server's own kubectl — nothing installed by the app.",
    "proxy": "Reverse-proxy manager: detects Caddy or Traefik. Shows status (running/stopped). "
    "Caddy: generate config templates (static site, reverse proxy, PHP-FPM) with copy button.",
    "tools": "Tools hub: one tap to any manager page.",
    "settings": "Settings: theme, monitoring, files, AI assistant, terminal, editor, security, "
    "backup & restore, fleet concurrency, per-server AI prompts.",
    "servers": "Connection manager: add/edit/duplicate/delete servers. Tags, color, jump host. "
    "Test button checks reachability. Import/export encrypted bundles.",
}
