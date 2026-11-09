# TAKVPS
**v1.3.0** — Client-only SSH/VPS manager (Flet + asyncssh). No backend; nothing installed on servers.

Manage your Linux servers from your phone or desktop. Connect via SSH, browse files, run commands,
monitor resources, manage Docker/Nginx/WireGuard/Kubernetes, chat with an AI assistant, and more —
all from one app, with nothing installed on the server.

## What's new in v1.3.0 — Modern UI refresh

This release modernizes the visual language across the whole app, applying research-backed
patterns from Material 3 Expressive, Linear, Vercel and Tailwind UI:

- **Design tokens** (`ui/theme.py`) — a single source of truth for the visual language:
  - 5-step spacing scale (XS=4, SM=8, M=12, LG=16, XL=24) — Tailwind-like rhythm.
  - 4-step corner-radius scale (SM=8, MD=12, M=16, LG=20) plus RADIUS_PILL=999.
  - 4-level elevation tokens (FLAT=0, LOW=1, MEDIUM=3, HIGH=5) → BoxShadow.
  - 3 icon-size tokens (default=22, LG=28, XL=48) for list items / heroes.
  - Density presets (compact/comfortable/spacious) — app defaults to COMPACT for high
    information density, Linear-style.
- **Semantic state colors** — `state_color(state, role)` and `status_to_state(status)` map
  arbitrary status strings (`running`, `exited`, `paused`, `connected`, ...) to 7 semantic
  states (ok/info/warning/error/critical/neutral/muted), each with container/on/plain
  color roles. Pages no longer hard-code `ft.Colors.GREEN` / `AMBER` everywhere.
- **Modern `common.py` components**:
  - `card()` — filled by default (M3), with subtle 1dp border. Pass `elevation=ELEVATION_LOW`
    for a raised card with BoxShadow.
  - `status_pill(label, state)` — small colored pill (22dp height) with optional dot or
    icon. Used by Docker containers, connections, processes, snippets.
  - `empty_state(icon, title, subtitle, action_label=, on_action=)` — modern centered
    empty state with optional primary action button (per Pencil&Paper / setproduct
    empty-state UX guidance).
  - `loading_state(label)` — centered ProgressRing + label.
  - `error_state(message, retry=)` — red icon + message + optional Retry button.
  - `skeleton_list(rows=)` — animated skeleton placeholders for list views
    (per NN/g skeleton-screen guidance).
  - `section_header(title, subtitle=, trailing=)` — page-section divider.
  - `page_header(title, subtitle=, actions=)` — large 22pt page title with trailing actions.
- **Modern `ConnectionBanner`** — calm error-container background, live colored status dot
  (amber=connecting, red=disconnected), Reconnect button only shown when the user can
  actually reconnect.
- **Modern topbar** (Linear/Vercel style) — selected icon buttons get a soft
  secondary-container pill background so the user can see "this panel is open" at a glance.
- **Modern mobile FAB** — Extended FAB with "Ask AI" label (Material 3 Expressive) on
  phones ≥400px wide; falls back to icon-only on very narrow screens.
- **Modern bottom navigation** — each destination has a `selected_icon` (filled variant),
  trailing slot is now "More" (opens the drawer) instead of "Settings".
- **Modern dialog helpers** (`context.py: trust / ask_yes_no / ask_text / ask_typed`):
  - Title uses `W_600` weight.
  - Body text uses the secondary surface-variant color (doesn't compete with title).
  - Monospace content (fingerprints, commands) wrapped in soft surface containers.
  - Destructive Confirm buttons are red.
  - All action buttons translated via `services.i18n` for Persian users.
- **Docker page** — modern empty/loading/error states per tab (skeleton list while
  loading, error_state with Retry on failure, contextual empty_state per tab with
  icons and starter hints), status pills instead of plain chips.
- **Processes page** — modern state machine (error > loading > empty > list), CPU/Mem
  shown as semantic-color status pills (green<50%, amber<90%, red≥90%).
- **Snippets page** — modern empty state with "Add a command" CTA, modern search-no-results
  state, risk-level pills use semantic colors (was: low-contrast plain-color chips with
  white text on amber), section headers use the new `section_header` component.
- **30 new regression tests** in `tests/test_modern_ui.py` covering design tokens,
  state-color lookups, status_pill/skeleton_list/empty_state/error_state/card APIs,
  and ConnectionBanner behavior.

## What's new in v1.2.0 — Persian/RTL UI

This release adds comprehensive Persian (Farsi) language support and right-to-left layout:

- **Page-level RTL** — when the user's language is `fa`, the entire layout flips to right-to-left
  (navigation rail moves to the right, padding/margins mirror, text right-aligns). This follows
  the W3C [Arabic & Persian Layout Requirements](https://w3c.github.io/alreq/) and the
  [Material 3 bidirectionality](https://m3.material.io/foundations/layout/bidirectionality-rtl)
  guidance.
- **Vazirmatn font** — the de-facto standard Persian UI font
  ([github.com/rastikerdar/vazirmatn](https://github.com/rastikerdar/vazirmatn)) is loaded from
  the jsDelivr CDN when Persian is active. Falls back to the platform's default sans-serif
  (Noto Sans Arabic on Linux, Tahoma on Windows, Geeza Pro on macOS) if the CDN isn't reachable.
- **2,425 Persian translation entries** (up from 151 in v1.0) covering navigation, file manager,
  firewall, networking, WireGuard, AI chat, terminal, settings, overview, and the first-run tour.
- **Persian help text** for every page — the topbar "?" button now opens a Persian explanation
  for users with language=fa.
- **Smart digit handling** — ordinal UI counts ("3 servers" → «۳ سرور») use Persian-Indic
  digits (U+06F0..U+06F9), while technical values (ports like `:22`, IPs like `10.0.0.1`,
  versions like `v1.2.3`, percentages like `75%`) stay in Western form. This matches Behdad
  Esfahbod's note that "in Iran people read and write western digits in a Latin context".
- **Half-space (ZWNJ, U+200C)** is used in all Persian compound words like «فایل‌ها», «می‌خواهم»
  per the Persian script convention.
- **Persian punctuation** — ، ؛ ؟ « » (Persian comma, semicolon, question mark, guillemets)
  used throughout the translation dictionary.
- **RTL icon mirroring** — directional icons (back/forward arrows, chevrons, first/last-page)
  swap automatically in RTL. Symmetric icons (refresh, search, settings, shield) stay as-is.
  Charts and progress bars stay LTR per Material 3 — only the linear progress fill direction
  mirrors.
- **Rail text bump** — Persian glyphs need slightly more vertical space than Latin (dots/marks
  above and below the baseline), so the rail label size was bumped from 10→11 and the rail width
  from 80→100 to give Persian labels like «پراکسی معکوس» room to render without ellipsis.
- **20 new regression tests** in `tests/test_persian_ui.py` covering translation coverage, digit
  conversion, technical-value detection, icon mirroring, RTL flag, and Persian help text.

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
- **Persian/RTL UI** (v1.2) — full right-to-left layout, Vazirmatn font, 2,425 Persian translations,
  smart Persian/Western digit handling, and Persian help for every page.

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
