# TAKVPS
Client-only SSH/VPS manager (Flet + asyncssh). No backend; nothing installed on servers.

**Phase 13** added persistent terminal sessions via tmux, a server-side Recycle
Bin, recursive folder downloads, drag-and-drop uploads (desktop), persistent transfer history,
native AI tool-calling, long-press on terminal rows to ask the AI, and a smart auto-scroll fix.

**Phase 14 (Part 1)** swaps the Monitor's bar sparklines for real `flet_charts.LineChart`
curves, with automatic fallback to the bar sparkline when the `flet-charts` package isn't
installed or when the user toggles it off in Settings → Monitoring.

**Phase 14 (Part 2)** turns the All-servers page from a read-only probe into a fleet
runner: tap ▶ on the Fleet page to run a one-off command or a saved snippet across every
saved server in parallel, with a live per-server progress dialog and a Stop-all button.

**Phase 14 (Part 3)** adds wide-character (CJK + emoji) glyph width support to the terminal
emulator, so `vim`, `nano`, `less` and `htop` render correctly with Chinese / Japanese /
Korean text and emoji. Combining marks attach to the previous glyph at zero width.

**Phase 14 (Part 4)** adds in-app SSH key generation — a "Generate new…" button in the
connection editor creates an ed25519 (or RSA) key pair in OpenSSH format, fills the private-key
field, and offers to install the public key on the connected server's `authorized_keys`.

**Phase 14 (Part 5)** adds per-server AI system prompts — each connection profile gains an
"AI instructions" field whose text is prepended to the assistant's system prompt when that server
is active, so you can write hard constraints like "Production — don't restart nginx during
09:00–17:00 UTC" that the model must follow.

**Phase 14 (scrollable toolbars fix)** makes every page's topbar / action-bar scroll
horizontally on narrow phones so buttons stay reachable instead of overflowing off-screen.

**Phase 14 (Part 6)** adds search-in-terminal-scrollback — a Find button in the terminal
toolbar opens a search bar with case-sensitive and regex toggles, live match count, and
prev/next navigation. Searches through the full scrollback + visible screen, including CJK
and emoji text.

**Phase 14 (Part 7)** adds tamper-evidence to the activity log — each record now carries a
SHA-256 chained hash, so editing a single line (to cover your tracks) breaks the chain and is
detected by the new "Verify integrity" button on the Activity page.

**Phase 14 (Part 8)** adds tool-call inspection — a "Show raw tool calls" toggle in Settings
renders the structured tool-call payloads (function name, args, result) as a collapsible block
on assistant messages that used native tool-calling, so you can see exactly what the model sent.

**Phase 14 (Part 9)** adds mouse reporting to the terminal — when vim/less/tmux enable mouse
mode (DECSET 1000/1003/1006), clicks and wheel-scrolls on the terminal output are sent to the
remote app as SGR mouse sequences, so you can click in vim or scroll in less without touching
the keyboard.

**Phase 14 (Part 10)** adds encrypted export bundles — one passphrase-encrypted text blob
contains the entire app state (connections + snippets + AI profiles + settings), so moving
to a new phone is one paste instead of five.

**Phase 14 (Part 11)** adds conversation branching — a "Branch from here" button on every
assistant message lets you fork the conversation at that point, discarding everything after
it so you can explore a different path.

**Phase 14 (Part 12)** adds lazy-load destination pages — the Activity page defers its disk
read to a background task (renders "Loading…" immediately), and every page build is wrapped
in try/except so a construction error shows a friendly message instead of crashing the app.

**Phase 15** adds: folder drag-and-drop upload (A1), configurable fleet concurrency (E2),
local-model-first AI routing (D2), per-page help "?" icon (G2), first-run tour (G1), cron
quick presets (C3), Linux + macOS CI builds (F2/F3), WireGuard manager (C4), Kubernetes
manager (C2), Caddy/Traefik reverse-proxy manager (C1), web mode docs (F4), and a demo
server stub (G3).

See the per-phase notes below for details.

## Run (desktop)
    pip install -r requirements.txt
    flet run main.py

## Test
    pytest

## Build APK
    flet build apk
`cryptography` (asyncssh's only native dependency) is resolved from pypi.flet.dev.

## CI
GitHub Actions workflows live in `.github/workflows/`:
- `tests.yml` — runs pytest on every push/PR (Python 3.10, 3.11, 3.12).
- `release.yml` — on a `v*` tag push, builds the Android APK (arm64-v8a) and Windows .exe
  distribution and creates a GitHub Release with both artifacts attached. Manual dispatch from
  the Actions tab builds without creating a Release.

## Phase 2 notes
- Credentials are encrypted at rest (Fernet). Default: key in app-private storage;
  optional passphrase mode (Settings → Security). See `storage/vault.py`.
- Export/import is copy-paste text encrypted with a passphrase you choose.
- Jump host: pick another saved server in the editor; chains are supported.

## Phase 3 notes
- Monitoring uses one SSH round-trip per poll (`/proc`, `df`, `ps`); rates are computed on the phone.
- Polling runs only while the Monitor page is visible; history (120 samples) lives in memory only.
- Charts are a dependency-free bar sparkline (`ui/components/gauge.py`).

## Phase 4 notes
- File manager uses SFTP only (no shell parsing); folder sizes are computed on demand (`du -sk`).
- Transfers are sequential; downloads write `<name>.part` and uploads a temp name, then rename,
  so cancelled/failed transfers never leave a half-written real file.
- Android storage: downloads go to the download folder in Settings (default: app-private storage).
  Public folders need the system picker; use Transfers → “Save a copy…”.
- Delete is permanent until the Recycle Bin (Phase 13). `/`, home and top-level system folders are
  protected; deleting a folder needs a typed confirmation.

## Phase 5 notes
- Editor: monospace multiline TextField for editing; "highlighted view" (palette button) shows
  syntax colours + line numbers (read-only). Flet can't colour text inside an input.
- Save keeps permissions, warns if the file changed on the server, optional `.takvps.bak` backup.
- Not atomic: the file is rewritten in place over SFTP.

## Phase 6 notes
- Terminal = PTY shell (asyncssh) + a built-in VT100/xterm emulator (`services/terminal.py`), so
  vim/nano/less/htop render. Limits: no wide (CJK/emoji) widths, no mouse, no charset switching.
- Typing goes through a one-line field (sentinel trick) so soft keyboards work; Esc/Tab/Ctrl/arrows
  etc. are in the toolbar. Shell history = ↑/↓ (the remote shell's own history).
- Sessions live in memory: they end if the SSH connection drops (persistent tmux sessions: Phase 13).

## Phase 7 notes (AI chat)
- Bring-your-own-key, client-side only: OpenAI-compatible (OpenAI/OpenRouter/Groq/Together/vLLM/Ollama/
  LM Studio/llama.cpp), Anthropic, Google Gemini. API keys are vault-encrypted like SSH credentials.
- Modes: Suggest only (default) · Ask each time · Agent (auto-runs only commands classified read-only;
  everything else needs approval; dangerous ones always need typed confirmation). Step limit + Stop button.
- Command protocol is "structured fallback": the model proposes one ```bash block per step; the app
  classifies it, applies the mode's approval rule, runs it over SSH, and feeds the output back.
  Native provider tool-calling is not used yet.
- Secrets are redacted (keys, tokens, passwords, .env values, URL credentials) before anything is sent;
  "What will be sent?" shows the exact redacted payload.
- AI-run commands are recorded in `activity.jsonl` (source = "ai"); the Activity Log screen comes later.
- The risk classifier is a safety net for human review, not a sandbox.

## Phase 8 notes (Commands)
- Snippets: name, multi-line command, working dir, sudo, background, group, icon/colour, global or
  per-server scope; `{{variables}}` are asked before running and inserted **shell-quoted** (don't
  wrap placeholders in quotes). Run history is kept locally (output tail per run).
- Risk gate on every run: dangerous → typed confirmation, destructive → confirm, writes → confirm only
  in Safe mode. Sudo password is asked once per connection, kept in memory only.
- Background runs use `nohup` with a log in /tmp/takvps-logs (tmux/screen integration arrives with
  Persistent Terminal Sessions in Phase 13).
- Git Deploy: pull → build steps → restart (systemd/docker/compose/command) → health check; on failure,
  offer a rollback (`git reset --hard <previous commit>` + rebuild + restart).
- Packs: export/import as JSON text; every command is risk-checked in a preview; built-ins included.
- Stop sends SIGTERM to the remote process (OpenSSH 7.9+ honours it; older servers may keep running).

## Phase 9 notes (Services · Cron · Packages · Updates)
- Everything is a UI over the server's own tools (`systemctl`, `crontab`, apt/dnf/yum/pacman/apk/zypper);
  no agent installed. Servers without systemd show a clear message.
- Root actions use sudo (asked once per connection, memory only). Unit files and other root-owned files are
  saved through the editor with `sudo install` (upload to /tmp, then install), then offer `daemon-reload`.
- Cron: schedule builder + human description + next-run preview (server time); jobs can be disabled
  (kept as `#takvps-disabled#` lines); untouched lines are preserved byte-for-byte; every save keeps a
  local backup (last 10) and `crontab` itself validates the file.
- Updates: security vs normal split on apt/dnf/yum/zypper (others list together); changelog preview on apt;
  reboot-required flag; "Security only" and "Update all" always ask for confirmation.
- apt/dpkg lock and interrupted-dpkg errors get a plain-language hint.

## Phase 10 notes (Docker · Nginx)
- Both managers detect whether the tool is installed. If not, **nothing is installed until you confirm**:
  read-only preflight (distro, architecture, free disk, RAM, sudo, internet, conflicting software) → a plan
  showing packages, disk estimate, service changes and the exact commands → explicit "Install" → live
  progress → verification (`docker --version`, daemon status, optional hello-world; `nginx -t`, service status).
- Packages come from the distro's own repositories via the native package manager. No `curl | sh`, no
  third-party repositories. Docker on RHEL-like distros (not in default repos) is reported as unsupported
  rather than guessed. Adding your user to the `docker` group is an explicit opt-in (it is root-equivalent).
- Docker: containers/images/volumes/networks/compose via the Docker CLI's JSON output; live logs, inspect,
  shell-in-terminal, stats toggle, prune (volumes/everything need a typed confirmation). If your user isn't in
  the docker group, commands run through sudo.
- Nginx: reload/restart always run `nginx -t` first. Editing a config tests it after save and offers a
  rollback to the previous text; enabling/disabling a site is automatically undone if the test fails.
  Templates: static site, reverse proxy, PHP-FPM. Optional certbot helper (installs certbot only after you
  confirm; it edits the nginx config to add HTTPS + redirect).

## Phase 11a notes (Logs · Firewall · Processes · Network · Users & keys · Quick actions · Activity)
- Logs: journald presets + discovered /var/log files; load or follow live, filter (text/regex/case), severity
  colouring, pause, copy/export. Uses sudo only when the plain read is denied.
- Firewall: ufw and firewalld are editable; iptables/nftables are read-only on purpose. **SSH lockout guard:**
  rules that allow your connection's port cannot be deleted/denied, `ufw enable` first adds an allow rule for
  your SSH port, firewalld can't remove `ssh`/your port. Every change is confirmed and shows the exact commands.
- Processes: signals (TERM/KILL/HUP/INT/STOP/CONT), renice, search/sort; PID 1, the sshd daemon and kernel
  threads need a typed confirmation. Auto-refresh stops when you leave the page.
- Network: listening ports (public vs local), connections by remote IP, ping/traceroute/DNS, public-IP lookup
  (done by the server, clearly labelled).
- Users & keys: users/sudoers, authorized_keys viewer with SHA256 fingerprints, add (validated public keys only)
  and remove; removing your last key while you log in by key needs a typed confirmation. Failed-login analysis.
- Quick actions: update, clear caches, free memory, vacuum journal, reboot, shutdown (all confirm; power actions typed).
- Activity: state-changing commands (privileged commands, snippets, deploys, installs, AI-run commands, quick
  actions) are logged locally with source and risk. Read-only queries are not logged.

## Phase 11b notes (Backups · Alerts · All servers · Search · Safe mode · App lock)
- Backups: `tar -czf` of a server folder into a folder you choose (nothing installed), with excludes, a size/free-space
  check, optional download to the phone and an optional daily cron job (with delete-after-N-days). Existing backups listed.
- Alerts: CRITICAL thresholds (CPU/RAM/disk) + watched services, debounced (2 checks in a row) with a 30-min cooldown per
  problem, evaluated only while the app is running. In-app notifications; OS notifications are a hook (`ctx.system_notify`).
- All servers: parallel, read-only probes of every saved server (never prompts; unknown host keys / missing passwords are
  skipped with a clear note). CPU is load ÷ cores.
- Search everything: pages, servers, commands, plus services/containers/sites/files seen this session (fuzzy ranking).
- Safe mode now asks before EVERY write path (files, editor, cron, keys, signals…) on top of the risk gates.
- App lock: PIN (salted PBKDF2; never stored), delays after 5 wrong tries, re-locks after the chosen time in the
  background. It hides the app; it does not encrypt credentials (use the passphrase option). No biometrics yet.

## Phase 12a notes (Health · .env · Databases · Tunnels · Disk usage)
- Health score: computed on the phone from one read-only command + the package manager's update list; each penalty
  explains itself and taps through to the page that fixes it.
- .env editor: parses/edits only the lines you touch (comments, order, `export` preserved), masks secret-looking keys,
  never logs or sends values anywhere, and offers a service/container restart after saving.
- Databases: MySQL/MariaDB, PostgreSQL, SQLite, Redis through the server's own CLI tools. Passwords are vault-encrypted
  and sent on stdin to a `read` wrapper (never in argv). SQL is classified: reads run, writes ask, DROP/TRUNCATE/DELETE
  without WHERE need a typed RUN, file/program-touching statements (INTO OUTFILE, COPY … PROGRAM, LOAD_FILE, \!) are blocked.
  Dumps are gzip files created owner-only; restore needs a typed RESTORE. Query text is not written to the activity log.
- SSH tunnels: local forwards bound to 127.0.0.1 only; stop when the connection drops; favorites per server.
- Disk usage: drill-down with bars + cleanup assistant (package caches, journal, rotated logs, unused Docker data, unused
  packages/old kernels on apt). Docker volumes need a typed CLEAN. Shows how much space was actually freed.

## Phase 13 notes (Persistent terminals · Recycle bin · Folder transfers · Drag-drop · Transfer history · Native AI · Long-press · Auto-scroll)
- **Persistent terminal sessions (tmux):** the Terminal page has a new "Sessions" toolbar button (hub icon) that lists
  tmux sessions on the server. Tap a session to attach in a new tab; long-press creates a new named session. Detach
  inside the session with Ctrl-B then D and it keeps running on the server — survive SSH drops, reconnect from any
  client. Falls back to a normal shell when `tmux` isn't installed (the dialog says so explicitly). Nothing is installed
  by the app; you install tmux yourself (`apt install tmux` / `dnf install tmux` / `pacman -S tmux`).
- **Recycle Bin:** delete now offers "Move to bin" (default) and "Delete forever". The bin lives at `~/.takvps-recycle/`
  on the server, with a sidecar `.meta/<entry>.json` recording the original path so restore is exact. Restore refuses
  to overwrite an existing file. Empty bin and per-entry "delete forever" both confirm first. The bin itself, `/`,
  and top-level system folders are protected. Auto-cleanup helper (`purge_older_than_command`) is available for cron.
- **Folder transfers (Two-Pane Transfer):** download now handles folders — the file manager walks the remote tree via
  SFTP, queues one mkdir step for the local tree, then enqueues each file as a normal transfer so progress/cancel
  still works. Same in reverse for upload (planned). Confirm dialog shows file count + total size before starting.
- **Drag-and-drop:** on desktop, dragging files from the OS file manager onto the Files page uploads them (uses Flet's
  `page.on_drop`). Mobile doesn't fire drop events, so the Upload button is still the path there.
- **Persistent transfer history:** finished transfers are appended to `transfer_history.json` (app-private, 200-item
  cap) and shown in a "History" section at the bottom of the Transfers dialog. Survives app restarts. Clear from the
  dialog or just leave it; it caps itself.
- **Native AI tool-calling (Phase 13.6):** the assistant now uses the provider's native function-calling protocol
  (OpenAI / Anthropic / Gemini) instead of parsing bash blocks from the model's text. The model proposes a structured
  `run_shell` tool call; the existing risk classifier + approval gate still apply, so dangerous commands still need
  typed confirmation. Falls back automatically to the Phase 7 structured (bash-block) path when the provider doesn't
  support tools or when the toggle in Settings → AI is off. Default: on.
- **Long-press → AI chat:** long-press any row in the terminal scrollback to open a context menu with "Send to AI"
  (sends the pressed row + 3 lines of context, marked with ▶) and "Copy line". Quicker than the existing "robot"
  button when you want the AI to focus on one specific line.
- **Smart auto-scroll:** the terminal output list no longer yanks you back to the bottom when new output arrives
  while you're scrolled up reading. Scroll back to the bottom (within 60 px) to resume following.

### Phase 13 bug fixes
- **SSH connection timeout bug:** `asyncssh.get_server_host_key()` doesn't accept `login_timeout` as a kwarg (it
  only takes `options=SSHClientConnectionOptions(...)`), so every fresh-connection attempt failed at the host-key
  check with `TypeError: got an unexpected keyword argument 'login_timeout'`. Fixed by wrapping the timeout in an
  options object (`core/ssh_client.py:_verify_host`).
- **Tools page unreachable on Android:** the Tools hub page was at index 5+ in the navigation list, so the phone
  bottom bar (3 slots + "More") showed Overview / Monitor / Files and buried Tools deep in the drawer. Fixed by
  bumping `BOTTOM_SLOTS` to 4 and migrating the saved `nav_order` to include "tools" at position 3 — now the
  bottom bar shows Overview / Monitor / Files / Tools + "More", so every manager is one tap away.

### Phase 13 caveats
1. **Folder upload via drag-drop** is single-level for now (Flet's `on_drop` reports files, not folders). Walking
   a dropped folder tree is Phase 14 work; for now drag files (or many files at once) and use the dialog flow for
   uploading folder-shaped things.
2. **Auto-scroll detection** uses `on_scroll`'s pixel offset; if a future Flet version changes the event shape,
   the fallback (`auto_scroll = True` briefly) is wrapped in try/except so it can't break rendering.
3. **Native tool-calling** requires the model itself to support tools, not just the provider. Old GPT-3.5 snapshots
   and some local Ollama models don't support tools — for those the request will succeed but no tool_calls come
   back, and the conversation falls through to the structured (bash-block) path on the next loop iteration.
4. **Recycle bin on encrypted / read-only filesystems:** the bin is created under `$HOME/.takvps-recycle/`. If your
   home is read-only or quota-limited, the move-into-bin step will fail with a clear error.

## Phase 14 Part 1 notes (Real line charts in the Monitor)
- The Monitor page now uses `flet_charts.LineChart` for the four history sparklines (CPU, RAM, network down,
  network up). Curved lines, no per-point markers, no axis labels (the card title already shows the live value),
  grid lines for readability.
- **Automatic fallback:** when `flet-charts` isn't importable (older install, slim APK), or when the user toggles
  the new "Use line charts" switch off in Settings → Monitoring, `LineChartSpark` transparently renders the
  Phase 3 bar sparkline instead. No code changes needed elsewhere.
- **Per-series colors:** CPU=green, RAM=amber, down=blue, up=teal — matches the Phase 3 palette so the upgrade
  looks consistent.
- **Fixed y-axis** for CPU/RAM (0–100%) so a CPU spike doesn't rescale the chart and hide the trend. Network
  series auto-scale with 15% headroom.
- The chart object is built once and only its `data_series` / `max_y` are swapped on each poll, so a 2-second
  poll interval is cheap (no full chart rebuild).
- New settings: `monitor_line_charts` (default ON). `requirements.txt` adds `flet-charts>=0.2`.
- 27 new tests in `tests/test_phase14.py` cover the pure chart helpers (`build_series`, `nice_y_max`,
  `pick_color`, `pick_stroke_width`, `last_value_str`, `should_use_line_charts`) and the fallback path of
  `LineChartSpark`.

## Phase 14 Part 2 notes (Multi-server fleet actions)
- The All-servers page gains a **▶ Run on fleet…** button. Tap it to open a picker with two paths:
  - **One-off command:** type `uptime` (or any single-line command), toggle `sudo`, hit "Run on all servers".
  - **Saved snippet:** pick from the global-scope snippets (variables are asked once and applied to every server).
- **One approval gate covers the whole fleet.** Dangerous commands still need typed confirmation
  (`RUN ALL`); destructive / writes ask for a regular confirm. Safe commands run with no prompt.
- **Bounded concurrency** (default 4) so a host with many saved servers doesn't exhaust file descriptors.
  Each server gets a per-run timeout (90 s default); a hung server is marked ERROR, not a stall.
- **Live dialog** shows one row per server with state icon (queued / connecting / running / done / failed /
  error / skipped / cancelled), the last line of stdout (or stderr / error), and a live summary line
  ("2 done · 1 running · 2 pending"). The dialog refreshes ~5×/s.
- **Stop-all** cancels every in-flight task and marks queued ones as CANCELLED. Already-finished rows keep
  their final state.
- **Unreachable servers are reported, not silently skipped.** Unknown host keys, missing passwords, and
  network timeouts all surface as ERROR with a clear one-line message — so you know which server needs a
  manual connect from the Servers page first.
- **Persistence:** every fleet run is appended to `fleet_history.jsonl` (one line per server, capped at
  2000 lines). The existing Activity page already shows fleet-run rows because each server is also logged
  via `ctx.activity.add(... source="fleet" ...)`.
- **Pure runner** in `services/fleet_runner.py` (no Flet, no asyncssh) is fully tested with 31 tests in
  `tests/test_phase14_part2.py` covering success / fail / error / timeout / skip / cancel / preflight /
  gate / concurrency cap / risk classification / ordering / history file IO.

## Phase 14 Part 3 notes (Wide-character support in the terminal)
- The VT100/xterm emulator in `services/terminal.py` now respects the Unicode `East_Asian_Width`
  property: CJK ideographs (中文汉字), Japanese kana (あいう / アイウ), Korean Hangul (한글), fullwidth
  Latin (ＡＢＣ１２３), and most emoji (😀 ❌ ✅ ⭐) occupy **two cells**, exactly like a real terminal.
- **Combining marks** (U+0300–U+036F, ZWJ U+200D, variation selectors U+FE00–U+FE0F) are zero-width and
  attach to the previous glyph — so `e` + combining acute renders as one cell, not two.
- **Internal representation:** a wide character is stored as `(char, style)` in the first cell and a
  `WIDE_CONT` sentinel (empty string) in the second cell. The renderer (`row_spans`, `spans_of_row`,
  `text()`) skips continuation cells, so copy/paste gives just the wide character with no gap.
- **Backspace** skips past continuation cells back to the wide char's first cell, matching real terminals.
- **Erasing** (CSI K, CSI J, CSI P) clears both halves of a wide character atomically — no dangling
  half-glyphs left behind.
- **Wrapping:** a wide character that doesn't fit in the remaining columns wraps to the next line
  (real terminals do the same; otherwise the glyph would render in one cell and misalign everything).
- **Resize** (`_fit`) drops orphaned wide chars cleanly when a row is truncated mid-pair — both the
  orphaned first-half and orphaned continuation are replaced with blanks.
- **Backward compatible:** all 9 original Phase 6 terminal tests still pass unchanged. ASCII text,
  ANSI colors, scroll regions, alternate screen, and cursor movement work exactly as before.
- **Pure helper** `services/wcwidth.py` (no Flet, no asyncssh) is fully tested with 52 tests in
  `tests/test_phase14_part3.py` covering width classification, combining marks, the `WIDE_CONT`
  sentinel, `string_width`, lru_cache behavior, and every emulator code path (put, backspace, wrap,
  erase, resize, row_spans, text, overwrite, ANSI colors with CJK, realistic vim/htop status lines).

## Phase 14 Part 4 notes (SSH key generation in-app)
- The connection editor gains a **"Generate new…"** button next to the existing "Import key file"
  button (visible when auth = SSH key). It opens a dialog where you pick the algorithm
  (ssh-ed25519 default, ssh-rsa optional) and an optional comment, then tap "Generate".
- **ed25519 is the default** — modern, tiny (~400-byte private key), fast, and supported by every
  OpenSSH 6.5+ server (released 2014). RSA 4096 is available as a fallback for very old servers.
- **The private key fills the connection editor's key field** automatically — from there it goes
  into the encrypted vault (same Fernet encryption as passwords) when you save the profile.
  The private key never leaves the device.
- **The public key is shown in the dialog** with its SHA256 fingerprint, so you can copy it
  manually if the server isn't connected yet.
- **"Install on server…" button** in the dialog: when you're connected to a server, it reads the
  logged-in user's `~/.ssh/authorized_keys`, checks the key isn't already installed, validates the
  format through the existing Phase 11a `validate_new_key` helper, confirms with you, then
  `mkdir -p ~/.ssh && chmod 700` + heredoc-append + `chmod 600`. The install is logged in the
  Activity page as an "action" entry.
- **Already-installed detection compares by blob** (not by the full line), so a different comment
  on the same key is still recognized as already installed — no duplicate entries.
- **No new dependencies**: uses `asyncssh.generate_private_key` (already in `requirements.txt`)
  for the actual keygen. The pure helper in `services/sshkeygen.py` wraps it with input validation,
  OpenSSH-format serialization, install-command building, and already-installed detection.
- 29 tests in `tests/test_phase14_part4.py` cover key generation (ed25519 + RSA), uniqueness,
  public-key parseability through the existing validator, default-comment building, public-key
  normalisation, install-command building, already-installed detection, and a full end-to-end
  generate → validate → install-command → detect roundtrip.

## Phase 14 Part 5 notes (Per-server AI system prompts)
- The connection editor gains an **"AI instructions"** textarea (under the color swatch). Text
  written there is prepended to the AI assistant's system prompt when that server is the active
  connection, wrapped in a `Notes about this server (follow these strictly):` block so the model
  treats it as a hard constraint rather than context.
- Use it for things like: "This server runs the production API — never run `systemctl restart`
  without asking first" or "The database on this box is the primary; don't run destructive SQL".
- The note is stored in the connection profile (`ai_context` field), encrypted at rest like the
  rest of the profile, and survives export/import, duplicate, and app restarts. Old profiles
  saved before Phase 14 Part 5 load with an empty `ai_context` (backward compatible).
- The note appears **before** the mode line (`suggest` / `ask` / `agent`) in the system prompt,
  so the model sees it as a top-level constraint. It appears **after** the base prompt (which
  describes the app and how to format bash blocks).
- The "What will be sent?" preview (chat panel → ⋮ → What will be sent?) now includes the
  per-server note in the `[system]` section, so you can verify exactly what the model sees.
- Empty notes (or whitespace-only) are skipped — no extra tokens consumed.
- 20 tests in `tests/test_phase14_part5.py` cover `build_system(server_note=...)`, the
  `ConnectionProfile.ai_context` field, storage round-trip (save/load/duplicate/old-format),
  the `_server_note()` helper, and a full end-to-end profile→build_system flow.

## Phase 14 scrollable toolbars fix
- Every page topbar (`ft.Row([ft.Text(title, expand=True), ...buttons...])`) now has
  `scroll=ft.ScrollMode.AUTO`, so when the buttons don't fit on a narrow phone screen the row
  scrolls horizontally instead of pushing buttons off-screen where they can't be tapped.
- Pages updated: shell `_topbar`, connections, fleet, docker, nginx, cron, firewall, processes,
  network, users, databases, services, activity, snippets, envedit, logs, and the chat panel's
  header + profile/mode row.
- A new `toolbar()` helper in `ui/components/common.py` wraps a scrollable Row; pages that
  already used `scroll=ft.ScrollMode.AUTO` (Files, terminal, editor, chat chips) are unchanged.
- The bottom NavigationBar (4 slots + "More") and the NavigationDrawer (opened by "More") are
  already scrollable by Flet's built-in handling — no change needed there.

## Phase 14 Part 6 notes (Search in terminal scrollback)
- The terminal toolbar gains a **🔍 Find** button. Tap it to open a search bar above the output
  area; type to search through the full scrollback + visible screen (up to 500 matches).
- **Case-insensitive by default**; toggle the `Aa` button for case-sensitive. Toggle the `</>`
  button for regex mode (invalid regexes are caught silently — no crash).
- **Live match count** in the status label: "3 of 12 matches" or "No matches." Updates on every
  keystroke.
- **Prev / Next buttons** (▲ ▼) jump between matches with wrap-around. Pressing Enter in the
  field also jumps to the next match.
- **Wide-character aware:** searches through the plain-text projection of each row, skipping
  `WIDE_CONT` sentinels (Phase 14 Part 3) so CJK and emoji text matches correctly. Match
  positions are character offsets, not cell offsets.
- **Line-oriented:** matches don't span row boundaries (terminal text is line-oriented). Each
  row is searched independently.
- **Caps at 500 matches** so a pathological search (e.g. matching every character in a 2000-line
  scrollback) doesn't freeze the UI.
- The search runs through `services/term_search.py` — a pure helper (no Flet, no asyncssh)
  that's fully tested with 44 tests in `tests/test_phase14_part6.py` covering pattern
  compilation, match finding (literal + regex + case-sensitive + wide-char + multi-match-per-row),
  next/prev navigation with wrap-around, `match_at_row`, `summary`, `is_valid_regex`, and
  realistic scenarios (log search, CJK log search, long-output search).

## Phase 14 Part 7 notes (Audit log tamper-evidence)
- Each activity log record now carries two extra fields: `prev_hash` (the hash of the previous
  record, or `"genesis"` for the first one) and `hash` (SHA-256 of `prev_hash + ":" + canonical_json(record)`).
- The chain is **tamper-evident, not tamper-proof** — an attacker with write access to
  `activity.jsonl` can still rewrite the whole file (including recomputing every hash). What they
  CAN'T do is edit a single record in place without also re-hashing every subsequent record. This
  catches the common case of "quietly edit one line to cover your tracks" and makes it visible.
- The Activity page gains a **✓ Verify integrity** button (shield icon) next to the Clear button.
  Tap it to check the chain: the result dialog shows "✓ 95 verified · 5 old (unverified)" when
  everything is intact, or "⚠ 1 tampered · 2 broken chain" when something's wrong.
- **Four verification statuses:**
  - `verified` — hash matches and chain link is intact.
  - `unverified` — no hash field (old record, pre-Phase-14-Part-7). Not tampered, just old.
  - `tampered` — hash doesn't match (someone edited the record's content after it was logged).
  - `broken_chain` — `prev_hash` doesn't match the previous record's hash (someone inserted or
    deleted a record).
- **Backward compatible:** old records without hash fields load fine and verify as UNVERIFIED
  (not tampered). The chain starts fresh from the first hashed record.
- **Corrupt JSON lines** (non-parseable) are handled gracefully — they're treated as records and
  don't crash the verifier.
- **Trim-safe:** when the log exceeds 3000 lines and trims to 2000, the last hash is re-read
  from the file so new records chain correctly. (The trimmed records themselves will show
  BROKEN_CHAIN if verified, since their `prev_hash` points to a now-deleted record — this is
  expected and not a security issue.)
- Pure helper `services/audit_chain.py` (no Flet, no asyncssh, stdlib `hashlib` + `json` only)
  is fully tested with 40 tests in `tests/test_phase14_part7.py` covering canonical JSON
  serialization, hash computation, chain building, verification (intact / tampered / deleted /
  inserted / old / mixed / corrupt / trimmed), summary helpers, and `ActivityLog` integration.

## Phase 14 Part 8 notes (Tool-call inspection)
- The `Msg` dataclass gains a `tool_calls` field — a list of dicts with `id`, `name`, `args`,
  and (after the command runs) `result_text` + `result_exit`. Stored as plain dicts so it
  survives JSON serialization in `ChatStore`.
- When native tool-calling (Phase 13.6) is active and the model emits tool calls, the
  `ChatController` attaches them to the assistant message. After each tool runs, the result is
  appended to the corresponding entry — so the full request→result cycle is captured.
- A new **"Show raw tool calls"** toggle in Settings → AI assistant (default OFF, since it's a
  debugging aid). When ON, assistant messages that used tool-calling show a collapsible
  `ExpansionTile` block with a monospace rendering of every tool call:
  ```
  run_shell  [call_abc123]
    command: df -h
    sudo: false
    → exit 0
    output:
      Filesystem  Size  Used  Avail  Use%
      /dev/sda1   20G   8G    12G    40%
  ```
- **Long values are truncated** for readability: commands > 200 chars, results > 500 chars or
  20 lines. Bool args render as `true`/`false` (not Python's `True`/`False`). Dict/list args
  render as compact JSON.
- **Backward compatible:** old saved messages (pre-Phase-14-Part-8) load with an empty
  `tool_calls` list. The structured-fallback (bash-block) path doesn't populate the field.
- Pure helper `services/tool_inspect.py` (no Flet, no asyncssh) is fully tested with 28 tests
  in `tests/test_phase14_part8.py` covering `format_tool_call` (basic, with/without result,
  long command/result truncation, missing id/args, bool formatting, dict args, declined result),
  `format_tool_calls` (empty, single, multiple-separated), count/has/summary helpers, the
  `Msg.tool_calls` field (default, set/get, to_dict/from_dict roundtrip, old-format load), and
  realistic end-to-end request→result cycles.

## Phase 14 Part 9 notes (Mouse reporting in the terminal)
- The terminal emulator now recognizes the DEC private modes for mouse reporting:
  - **DECSET 1000** — report button presses (button mode).
  - **DECSET 1003** — report presses + releases + motion (any-event mode; used by vim).
  - **DECSET 1006** — SGR encoding (the format we emit).
  - **DECSET 1015** — urxvt encoding (accepted but we always emit SGR).
- When a remote app (vim, less, tmux) enables mouse reporting, the terminal output's Container
  captures `on_tap_down` (clicks), `on_click` (fallback), and `on_hover` (motion) events. Pixel
  coordinates are converted to 1-based terminal cell coordinates (col, row) using the same
  font-metric formula as `TerminalController.size()` (cell width ≈ 0.6 × font size).
- **SGR format**: `\x1b[<{code};{x};{y}M` for press, `...m` for release. The `code` encodes
  the button (0=left, 1=middle, 2=right, 64=wheel-up, 65=wheel-down) plus modifiers (Shift=4,
  Alt=8, Ctrl=16).
- **Motion events** (mode 1003) are sent only when `mouse_any` is on — vim uses this for
  mouse-aware cursor positioning.
- **Coordinates are clamped to 1–224** for safety (the legacy coordinate range; some apps
  still expect it even in SGR mode).
- **No-op when mouse reporting is off** — clicks on the terminal output do nothing when the
  remote app hasn't requested mouse events, so normal terminal behaviour (text selection,
  scroll) is unaffected.
- Pure helper `services/mouse.py` (no Flet, no asyncssh) is fully tested with 51 tests in
  `tests/test_phase14_part9.py` covering SGR encoding (all buttons, wheel, modifiers, release,
  clamping), convenience functions, mode detection, `should_report` logic, SGR response parsing,
  Flet button-name mapping, `Screen` mouse-mode flags (enable/disable/reset/urxvt), and realistic
  vim/less/tmux scenarios.

## Phase 14 Part 10 notes (Encrypted export bundles)
- Settings → Backup & restore has two buttons: **Export bundle…** and **Import bundle…**.
- **Export** collects the entire app state — connections (with decrypted secrets), snippets,
  deploys, AI profiles (with decrypted API keys), and settings — into one JSON blob, then
  encrypts it with a user-chosen passphrase. The encrypted blob is shown in a text field with
  a Copy button.
- **Import** takes a pasted blob + the passphrase, decrypts it, shows a preview (e.g. "Bundle
  from 2024-10-30 · 5 connections · 12 snippets · 3 AI profiles"), and on confirmation adds
  everything to the current device. Existing data is NOT deleted — the bundle is merged.
- **Secrets are decrypted from the vault** before bundling (so they're re-encrypted with the
  passphrase, not the device's vault key). The bundle never touches disk in plaintext form.
- **`known_hosts` is excluded** — those are device-specific trust decisions and shouldn't be
  transferred to a new phone.
- **IDs are remapped** on import so there are no collisions with existing data.
- 10 tests in `tests/test_phase14_part10.py` cover collect, encrypt/decrypt roundtrip, wrong
  passphrase, corrupt blob, metadata, apply (merge without delete), secrets decryption, empty
  bundle, settings inclusion, and known_hosts exclusion.

## Phase 14 Part 11 notes (Conversation branching)
- Every assistant message that has messages after it shows a **"Branch from here"** button
  (with a call-split icon) below the content.
- Tapping it opens a confirm dialog showing the last message's text and a summary
  ("Keep 5 messages, discard 3"). On confirmation, the conversation is truncated to that
  point — everything after is removed.
- The user can then type a different follow-up and explore an alternative path from the same
  context, without having to re-explain the situation.
- Only assistant messages are branchable (user messages and result messages aren't useful
  branch points). The last message in the conversation isn't branchable (nothing to discard).
- 17 tests in `tests/test_phase14_part11.py` cover `branch_from`, `find_branchable`,
  `branch_summary`, `can_branch`, and edge cases (empty, out of range, no mutation, singular).

## Phase 14 Part 12 notes (Lazy-load destination pages)
- The **Activity page** now defers its disk read (`activity.recent(300)`) to a background
  task (`page.run_task`), so the page renders immediately with a "Loading…" placeholder instead
  of blocking the UI while the file is read. The data loads ~10ms later and the list updates
  in place.
- The **Shell's `render()`** method wraps every page build in `try/except` — if a page's
  constructor throws (e.g. because the server doesn't support a feature the page expects), a
  friendly error message is shown instead of crashing the app. The error text is selectable so
  the user can copy it and report it.
- 5 tests in `tests/test_phase14_part12.py` cover the lazy-loading pattern (records loaded
  into empty list, doesn't block) and the error-catching wrapper (catches construction errors,
  truncates long messages, no false positive for valid pages).

---

## Phase 14 — Complete

All 12 planned parts are done:

| Part | Feature | Tests |
|------|---------|-------|
| 1 | Real line charts in the Monitor (A2) | 27 |
| 2 | Multi-server fleet actions (C5) | 31 |
| 3 | Wide-character support in the terminal (A3) | 52 |
| 4 | SSH key generation in-app (B4) | 29 |
| 5 | Per-server AI system prompts (D1) | 20 |
| 6 | Search in terminal scrollback (A5) | 44 |
| 7 | Audit log tamper-evidence (B3) | 40 |
| 8 | Tool-call replay & inspection (D4) | 28 |
| 9 | Mouse reporting in the terminal (A4) | 51 |
| 10 | Encrypted export bundles (B2) | 10 |
| 11 | Conversation branching (D3) | 17 |
| 12 | Lazy-load destination pages (E1) | 5 |
| Bonus | Scrollable toolbars fix | — |

**Total: 552 tests, 145 Python files, all passing.**

