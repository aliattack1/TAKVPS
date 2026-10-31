# Phase 14 — Ideas & Candidate Features

> **ALL PARTS COMPLETE.** Parts 1–12 + scrollable toolbars fix = **552 tests, 145 Python files.**
> 
> | Part | Feature | Status |
> |------|---------|--------|
> | 1 | A2 — Real line charts in the Monitor | ✅ |
> | 2 | C5 — Multi-server fleet actions | ✅ |
> | 3 | A3 — Wide-character support in the terminal | ✅ |
> | 4 | B4 — SSH key generation in-app | ✅ |
> | 5 | D1 — Per-server AI system prompts | ✅ |
> | 6 | A5 — Search in terminal scrollback | ✅ |
> | 7 | B3 — Audit log tamper-evidence | ✅ |
> | 8 | D4 — Tool-call replay & inspection | ✅ |
> | 9 | A4 — Mouse reporting in the terminal | ✅ |
> | 10 | B2 — Encrypted export bundles | ✅ |
> | 11 | D3 — Conversation branching | ✅ |
> | 12 | E1 — Lazy-load destination pages | ✅ |
> | Bonus | Scrollable toolbars fix | ✅ |

After Phase 13, the TAKVPS app has reached a fairly complete state: every phase 1–13 feature
is implemented, 197 unit tests pass, and the Android + Windows release builds are wired up in
CI. Here are the candidate directions for Phase 14, ranked by user impact and grouped by
theme. Pick one or two for the next iteration rather than trying to do them all.

---

## A. Polish & UX (low risk, high visibility)

### A1. Folder upload via drag-and-drop
Phase 13.4 only handles dropped files. Walking a dropped folder tree on desktop needs
`page.on_drop` to expose folder paths (or falling back to a manual recursive upload flow).
**Effort:** small. **Impact:** completes the file-transfer story.

### A2. Real line charts in the Monitor ✅ DONE (Part 1)
Phase 3 used a custom bar sparkline because Flet's chart controls weren't confirmed to ship
in 1.0. Now that Flet 1.0.5 is the baseline (see `requirements.txt`), try `flet.charts.LineChart`
for CPU/RAM/disk history. The bar sparkline stays as a fallback if the chart module is missing.
**Effort:** small. **Impact:** big visual upgrade for the most-used page.
**Implementation:** `services/charts.py`, `ui/components/gauge.LineChartSpark`, toggle in Settings → Monitoring.

### A3. Wide-character support in the terminal ✅ DONE (Part 3)
Phase 6 noted CJK and emoji misalign. The emulator in `services/terminal.py` counted every
character as 1 cell. Added a `wcwidth`-style width lookup (Python's `unicodedata.east_asian_width`
as the data source; pure-Python, no new deps) so vim/nano/less render properly with CJK text
and emoji. Combining marks attach to the previous glyph at zero width.
**Effort:** small. **Impact:** important for non-English users.
**Implementation:** `services/wcwidth.py`, `services/terminal.py` (`_put`, `_ground` backspace,
`_erase_line`, `_fit`, `row_spans`, `spans_of_row`, `text`), 52 tests in `tests/test_phase14_part3.py`.

### A4. Mouse reporting in the terminal ✅ DONE (Part 9)
Added mouse reporting to the terminal emulator. When a remote app (vim/less/tmux) enables
mouse mode via DECSET 1000 (button) / 1003 (any-event) / 1006 (SGR encoding), clicks and
wheel-scrolls on the terminal output are captured and sent as SGR mouse sequences. Pixel
coordinates are converted to terminal cell coordinates using the font-metric formula.
**Effort:** medium. **Impact:** desktop power-user feature.
**Implementation:** `services/mouse.py` (pure SGR encoder), `Screen.mouse_*` mode flags in
`services/terminal.py`, `on_tap_down`/`on_hover` handlers in `ui/pages/terminal.py`, 51 tests
in `tests/test_phase14_part9.py`.

### A5. Search in terminal scrollback ✅ DONE (Part 6)
Added a "🔍 Find" button to the terminal toolbar that opens a search bar with case-sensitive
and regex toggles, live match count ("3 of 12 matches"), and prev/next navigation with
wrap-around. Searches through the full scrollback + visible screen, including CJK and emoji
text (uses the Phase 14 Part 3 wide-char projection). Caps at 500 matches for performance.
**Effort:** small. **Impact:** big for long-running commands.
**Implementation:** `services/term_search.py` (pure helper), find bar in `ui/pages/terminal.py`,
44 tests in `tests/test_phase14_part6.py`.

---

## B. Safety & security (high stakes, careful work)

### B1. Biometric app lock
Phase 11b's app lock is PIN-only. Add fingerprint/face unlock via Flet's `page.services`
biometrics hook on Android (where available), with a PIN fallback. Sensitive operations still
require the PIN.
**Effort:** medium. **Impact:** quality-of-life on Android.

### B2. Encrypted export bundles ✅ DONE (Part 10)
Phase 2's export uses a passphrase-encrypted text bundle. Added an option to export the *entire*
app state (servers, snippets, AI profiles, settings) as one encrypted bundle, so moving to a
new phone is one paste instead of five. Secrets are decrypted from the vault and re-encrypted
with the user's passphrase. IDs are remapped on import (merge without delete). 10 tests.
**Effort:** medium. **Impact:** big for users with many servers.

### B3. Audit log tamper-evidence ✅ DONE (Part 7)
The activity log (`activity.jsonl`) is append-only but was not tamper-evident. Added a chained
SHA-256 hash (like a tiny blockchain) so a server admin can't quietly edit the log to cover
their tracks. Each record carries `prev_hash` and `hash` fields; editing a single line breaks
the chain and is detected by the new "Verify integrity" button on the Activity page.
**Effort:** small. **Impact:** important for compliance-oriented users.
**Implementation:** `services/audit_chain.py` (pure helper), `ActivityLog.verify()` in
`storage/activity.py`, "Verify integrity" button in `ui/pages/activity.py`, 40 tests in
`tests/test_phase14_part7.py`.

### B4. SSH key generation in-app ✅ DONE (Part 4)
Right now you have to generate SSH keys outside the app and paste them. Added a "Generate new…"
button in the connection editor that runs `asyncssh.generate_private_key('ssh-ed25519')` (already
a dep), fills the private-key field, and offers to install the public key on the connected server
via the existing Phase 11a `authorized_keys` flow. RSA 4096 is available as a fallback.
**Effort:** medium. **Impact:** smooth onboarding for new users.
**Implementation:** `services/sshkeygen.py`, "Generate new…" button in `ui/pages/connections.py`,
"Install on server…" flow via `authorized_keys`, 29 tests in `tests/test_phase14_part4.py`.

---

## C. New managers (medium-large features)

### C1. Reverse-proxy & SSL manager (Caddy / Traefik)
Phase 10 has nginx. Caddy and Traefik are increasingly common and have nicer UX for HTTPS.
A unified "Sites" page that detects which is installed and offers the same templates (static
site, reverse proxy, PHP-FPM) would close the gap.
**Effort:** large. **Impact:** broadens server coverage.

### C2. Kubernetes manager
For users running k8s on their VPS. List pods / deployments / services, view logs, exec into a
pod (via the existing terminal). The risk classifier already knows `kubectl` subcommands.
**Effort:** large. **Impact:** high-value but niche.

### C3. Cron expression builder GUI
Phase 9 has a cron schedule builder, but it's text-based. A visual builder (every-N-minutes,
hourly, daily-at-time, weekly, monthly patterns) with a live "next 5 runs" preview would be
friendlier. The parser is already there.
**Effort:** medium. **Impact:** nice for non-cron-fluent users.

### C4. WireGuard / VPN manager
Build on the SSH-tunnels work (Phase 12a) to manage a WireGuard setup on the server:
generate keys, configure peers, show connection status, start/stop the interface. Stays
client-only — just edits `/etc/wireguard/wg0.conf` over SSH.
**Effort:** large. **Impact:** high for self-hosters.

### C5. Multi-server fleet actions ✅ DONE (Part 2)
Phase 11b's "All servers" page is read-only. Extend it to run a snippet across N servers in
parallel, with a live per-server progress view and a "stop all" button. Risk-classified and
approval-gated like single-server runs.
**Effort:** medium. **Impact:** big for users with many small VPSes.
**Implementation:** `services/fleet_runner.py`, `storage/fleet_history.py`, `ui/pages/fleet.py` (▶ button + live dialog), 31 tests in `tests/test_phase14_part2.py`.

---

## D. AI assistant improvements

### D1. Per-server AI system prompts ✅ DONE (Part 5)
Right now the system prompt is generic. Added an optional per-server "AI instructions" field
(saved in the connection profile as `ai_context`) so the AI knows things like "this server runs
the production blog, don't restart nginx during 9–5 UTC". The note is prepended to the system
prompt in a `Notes about this server (follow these strictly):` block so the model treats it as a
hard constraint.
**Effort:** small. **Impact:** reduces false-positive dangerous suggestions.
**Implementation:** `ConnectionProfile.ai_context` field, `build_system(server_note=...)` in
`services/ai/chat.py`, "AI instructions" textarea in `ui/pages/connections.py`, 20 tests in
`tests/test_phase14_part5.py`.

### D2. Local-model-first routing
Phase 7 supports local Ollama / LM Studio / vLLM. Add a "prefer local model" setting that
auto-routes to the local profile when the device is on the same network as the server (cheap
latency check), and falls back to the cloud profile otherwise. Saves API costs.
**Effort:** small. **Impact:** cost saver for daily-driver users.

### D3. Conversation branching ✅ DONE (Part 11)
Added a "Branch from here" button on every assistant message (with a call-split icon). Tapping
it truncates the conversation to that point, discarding everything after. Confirm dialog shows
what will be kept/discarded. Only assistant messages with followers are branchable. 17 tests.
**Effort:** medium. **Impact:** nice for debugging sessions.

### D4. Tool-call replay & inspection ✅ DONE (Part 8)
Phase 13.6's native tool-calling hides the structured tool_call/tool_result payloads from the
user. Added a "Show raw tool calls" toggle in Settings → AI assistant that renders the structured
payloads inline as a collapsible `ExpansionTile` block on assistant messages that used
tool-calling. Shows function name, call id, args (with bool/dict formatting), result exit code,
and the output (truncated for readability). Off by default — only enable for debugging.
**Effort:** small. **Impact:** transparency for power users.
**Implementation:** `Msg.tool_calls` field in `services/ai/chat.py`, `services/tool_inspect.py`
(pure formatter), "Show raw tool calls" toggle in Settings, collapsible block in
`ui/pages/chat.py`, 28 tests in `tests/test_phase14_part8.py`.

---

## E. Performance & scalability

### E1. Lazy-load destination pages ✅ DONE (Part 12)
The Activity page now defers its disk read to a background task (renders "Loading…" immediately).
The Shell's render() wraps every page build in try/except so construction errors show a friendly
message instead of crashing the app. 5 tests.
**Effort:** small. **Impact:** faster cold start, especially on Android.

### E2. Connection pool for fleet operations
Phase 11b's "All servers" probe opens N parallel SSH connections, which can exhaust file
descriptors on low-ulimit systems. Use a bounded `asyncio.Semaphore` (cap at, say, 8) and a
shared `asyncssh` connection pool.
**Effort:** small. **Impact:** stability with many servers.

### E3. Streaming terminal rendering
Phase 6 throttles renders to ~15 FPS via `await asyncio.sleep(0.06)`. For very fast output
(e.g. `yes`), this still re-renders every changed row. Switch to a dirty-rect / viewport-only
render: only re-render the rows currently visible in the scroll viewport, not all of them.
**Effort:** medium. **Impact:** smoothness for `htop` / `tail -f` workloads.

---

## F. Platform & distribution

### F1. iOS build
Phase 13's CI builds Android + Windows. iOS is technically possible with Flet but needs a
macOS runner (more expensive) and an Apple Developer account. Worth it if there's demand.
**Effort:** large (mostly CI + signing). **Impact:** new platform.

### F2. Linux desktop build
Add a `flet build linux` job to the release workflow. Mostly free since the runner is already
Ubuntu; produces a tarball with the bundled Python + app.
**Effort:** small. **Impact:** closes the desktop-platform set.

### F3. macOS build
Same as Linux but on a macOS runner. Apple-notarized builds would need an Apple ID secret.
**Effort:** medium. **Impact:** macOS users (probably the largest desktop audience).

### F4. Web/desktop app via Flet web
Flet can serve the app over HTTP. A web mode would let users run TAKVPS from a browser
pointed at their dev machine — useful for "I'm on someone else's computer" scenarios. Risk:
exposing the SSH credentials vault over HTTP needs careful thought.
**Effort:** large. **Impact:** nice-to-have, security trade-offs.

---

## G. Documentation & onboarding

### G1. In-app "first run" tour
A 4-card swipe-through intro on first launch: connect a server, browse files, try the AI,
where to find Tools. Skippable. Reduces the "what does this app do?" bounce rate.
**Effort:** small. **Impact:** first-impression.

### G2. Per-page help
A small "?" icon in each page's topbar that opens a one-paragraph explainer for what the page
does and its key shortcuts/buttons. Pulls from a `docs/` JSON file.
**Effort:** small. **Impact:** discoverability.

### G3. Demo server
Bundle a read-only "demo" connection profile (pointing at a sandboxed test VPS) so users can
explore the app before connecting their own server. Would need someone to host and maintain
the demo, or stub it with fake data.
**Effort:** large (operational). **Impact:** trial-before-buy.

---

## Recommendation for the next part

**ALL PLANNED PARTS ARE COMPLETE.** Phase 14 is done: 12 parts + scrollable toolbars fix,
552 tests passing, 145 Python files. The remaining items in this document (A1, B1, C1–C4, D2,
E2–E3, F1–F4, G1–G3) are candidates for Phase 15+ if the user wants to continue.
