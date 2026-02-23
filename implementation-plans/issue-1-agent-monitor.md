# Implementation Plan: Agent Monitor TUI

**Issue**: #1 — Agent Monitor: Multi-Instance Claude Code Monitoring TUI
**Date**: 2026-02-22

## Overview

Build a Python Textual TUI that monitors all Claude Code instances across Hyprland
workspace groups, showing real-time status via Hyprland event socket and optional
rich data via Claude Code's statusline feature.

## Environment Notes

- Python 3.14.2t (free-threading) is the default via asdf; 3.12.11 and 3.13.11 also available
- No `uv` installed; `pip` available via asdf
- Target Python >=3.12 for broad compatibility
- Use a standard venv for development

---

## Phase 1: Project Scaffolding ✅

### Step 1.1: Create pyproject.toml

```toml
[project]
name = "agent-monitor"
version = "0.1.0"
description = "TUI dashboard for monitoring Claude Code instances across Hyprland workspaces"
requires-python = ">=3.12"
dependencies = [
    "textual>=1.0.0",
    "watchfiles>=1.0.0",
]

[project.scripts]
agent-monitor = "agent_monitor.app:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Step 1.2: Create directory structure

```
agent-monitor/
├── pyproject.toml
├── src/
│   └── agent_monitor/
│       ├── __init__.py
│       ├── app.py
│       ├── hyprland.py
│       ├── statusline.py
│       ├── models.py
│       ├── workspace.py
│       └── monitor.tcss
├── scripts/
│   └── statusline-sidecar.sh
└── tests/
    ├── __init__.py
    ├── test_models.py
    └── test_hyprland.py
```

### Step 1.3: Set up development environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Add a `[project.optional-dependencies]` section for dev deps:
```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "textual-dev>=1.0.0"]
```

### Step 1.4: Create .gitignore

Standard Python .gitignore: `.venv/`, `__pycache__/`, `*.egg-info/`, `dist/`, `.dev_tools/`, `.claude/hooks/`, `.claude/commands/`, `.claude/settings.local.json`

---

## Phase 2: Data Models (`src/agent_monitor/models.py`) ✅

### Step 2.1: Define AgentState enum

```python
from enum import Enum

class AgentState(Enum):
    ACTIVE = "active"       # Braille spinner spinning
    IDLE = "idle"           # ✳ shown, waiting for input
    ATTENTION = "attention" # 🔔 bell prefix, needs user action
```

### Step 2.2: Define AgentSession dataclass

Fields:
- `address: str` — Hyprland window address (normalized, no `0x`)
- `session_name: str` — Zellij session name (from window title)
- `task_description: str` — Current task text
- `state: AgentState` — Current status
- `workspace_id: int` — Raw Hyprland workspace ID
- `workspace_group: int` — Computed (`workspace_id % 10`)
- `window_class: str` — Terminal emulator class name
- `pid: int | None` — Process ID (None when discovered via `openwindow` event; backfilled on next `hyprctl clients -j` refresh)
- Optional statusline fields (all `Optional`):
  - `cost_usd: float | None`
  - `duration_ms: int | None`
  - `context_used_pct: float | None`
  - `model_name: str | None`
  - `lines_added: int | None`
  - `lines_removed: int | None`

### Step 2.3: Define constants

```python
BRAILLE_SPINNER_CHARS = frozenset({'\u2802', '\u2810'})  # ⠂ ⠐
IDLE_CHAR = '\u2733'  # ✳
ATTENTION_EMOJI = '\U0001F514'  # 🔔
TERMINAL_CLASSES = frozenset({
    'Alacritty', 'com.mitchellh.ghostty', 'kitty', 'foot', 'org.wezfurlong.wezterm'
})
MONITOR_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "claude-monitor")
```

Note: Use `$XDG_RUNTIME_DIR/claude-monitor/` instead of `/tmp/claude-monitor/` for better
security (per-user, `0700` permissions, no symlink concerns). The sidecar script and watcher
must use the same path.

### Step 2.4: Define title parsing function

`parse_window_title(title: str, window_class: str) -> dict | None`

Logic:
1. Reject if `window_class` not in `TERMINAL_CLASSES`
2. Reject if no ` | ` in title
3. Split on first ` | `
4. Check for `🔔` prefix on session name
5. Check first char after pipe is a known status char (✳, ⠂, ⠐)
6. Return dict with `session_name`, `state`, `task_description`, `has_attention`

### Step 2.5: Write tests for title parsing

Test cases:
- Standard active: `"ge-class | ⠐ Browser Game Implementation"` → ACTIVE
- Standard idle: `"iam-catalog | ✳ Claude Code"` → IDLE
- Attention: `"🔔 ge-play-narrative | ✳ Browser Testing"` → ATTENTION
- Attention + active: `"🔔 ge-play-narrative | ⠐ Browser Testing"` → ATTENTION
- Non-Claude session: `"BoK | jesse@office:~/projects/bok"` → None
- No pipe: `"Firefox"` → None
- Non-terminal class: title with pipe but class `"firefox"` → None

---

## Phase 3: Hyprland Integration (`src/agent_monitor/hyprland.py`)

### Step 3.1: Socket path discovery

`get_event_socket_path() -> str`

1. Read `$HYPRLAND_INSTANCE_SIGNATURE` env var
2. Construct path: `/run/user/{uid}/hypr/{sig}/.socket2.sock`
3. Fallback: scan `/run/user/{uid}/hypr/` for subdirs with `.socket2.sock`
4. Raise `FileNotFoundError` if not found

### Step 3.2: Initial state fetch via hyprctl

`async fetch_clients() -> list[dict]`

Run `hyprctl clients -j` via `asyncio.create_subprocess_exec` with a 5-second timeout,
parse JSON. Log warnings on non-zero exit or timeout; return empty list on failure.

For each client with a matching window title, create an `AgentSession`.

Normalize addresses: strip `0x` prefix. Skip negative workspace IDs (special workspaces).
Skip windows where `workspace_id % 10 == 0` (workspace IDs 10, 20, 30 don't belong to any
valid group in the 1-9 system — these workspaces are not used in the current setup).

### Step 3.3: Event socket listener

`async listen_events(socket_path: str, callback: Callable)`

1. Connect via `asyncio.open_unix_connection(socket_path)`
2. Read data in a loop, buffer until `\n`
3. Parse event format: `EVENT_NAME>>DATA`
4. Handle events:
   - `windowtitlev2`: split on first comma → `(address, title)`. Call callback with title update.
   - `openwindow`: split on first 3 commas → `(address, ws_id, class, title)`. Register new window.
   - `closewindow`: remove window from tracking.
   - `movewindowv2`: split on first 2 commas → `(address, ws_id, ws_name)`. Update workspace.
5. Reconnect on disconnection with exponential backoff (1s, 2s, 4s, max 10s)

### Step 3.4: HyprlandMonitor class

Encapsulates all Hyprland interaction. Manages:
- `sessions: dict[str, AgentSession]` — keyed by normalized address
- `_window_meta: dict[str, dict]` — class, workspace, pid per window (for all windows, not just Claude)
- Callbacks: `on_session_update`, `on_session_remove` — called when sessions change

Methods:
- `async start()` — fetch initial state, then start event listener
- `async refresh()` — re-fetch all clients via hyprctl (for periodic full sync)
- `_handle_title_change(addr, title)` — parse title, update/create/remove session
- `_handle_window_open(data)` — register new window metadata
- `_handle_window_close(addr)` — clean up window and session
- `_handle_window_move(addr, ws_id)` — update workspace group

---

## Phase 4: Statusline File Watcher (`src/agent_monitor/statusline.py`)

### Step 4.1: StatuslineWatcher class

Watches `$XDG_RUNTIME_DIR/claude-monitor/` for `.json` file changes using `watchfiles.awatch()`.

Fields:
- `sessions: dict[str, dict]` — keyed by session name (filename without `.json`), value is parsed JSON
- `on_update: Callable` — callback when data changes

### Step 4.2: Watch loop

```python
async def watch(self):
    Path(MONITOR_DIR).mkdir(parents=True, exist_ok=True)
    # Read existing files on startup
    for f in Path(MONITOR_DIR).glob("*.json"):
        self._read_file(f)
    # Watch for changes
    async for changes in awatch(MONITOR_DIR, debounce=400, recursive=False):
        for change_type, filepath in changes:
            if not filepath.endswith(".json") or Path(filepath).name.startswith("."):
                continue
            if change_type in (Change.added, Change.modified):
                self._read_file(Path(filepath))
            elif change_type == Change.deleted:
                name = Path(filepath).stem
                self.sessions.pop(name, None)
                if self.on_update:
                    self.on_update(name, None)
```

### Step 4.3: File reading with error handling

`_read_file(path: Path)`:
1. Read file content
2. Parse JSON, catch `JSONDecodeError` and `FileNotFoundError` (race with atomic write)
3. Extract key fields: `cost.total_cost_usd`, `cost.total_duration_ms`, `context_window.used_percentage`, `model.display_name`, `cost.total_lines_added`, `cost.total_lines_removed`
4. Store in `self.sessions[name]`
5. Call `self.on_update(name, data)`

---

## Phase 5: Workspace Switching (`src/agent_monitor/workspace.py`)

### Step 5.1: switch_to_group function

`async switch_to_group(group: int) -> None`

1. Validate group is 1-9
2. Run `workspace-group {group}` via `asyncio.create_subprocess_exec` with 3-second timeout
3. Look up `workspace-group` on PATH (it lives in `~/bin/` or similar via omarchy-setup)
4. Log warning on non-zero exit or timeout; do not crash the app

### Step 5.2: Focus window function

`async focus_window(address: str) -> None`

Run `hyprctl dispatch focuswindow address:0x{address}` to focus the specific terminal window
after switching groups. Use 3-second timeout and log warnings on failure (same error handling
pattern as `switch_to_group`).

---

## Phase 6: TUI Application (`src/agent_monitor/app.py` + `monitor.tcss`)

### Step 6.1: AgentMonitorApp class (Textual App)

```python
class AgentMonitorApp(App):
    TITLE = "Agent Monitor"
    CSS_PATH = "monitor.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "switch_group", "Switch"),
    ]
```

### Step 6.2: Compose the layout

Simple layout — just a DataTable filling the screen, with a header showing summary stats and a footer with key hints.

```python
def compose(self) -> ComposeResult:
    yield Header()
    yield DataTable(id="sessions")
    yield Footer()
```

### Step 6.3: DataTable setup on mount

On mount:
1. Configure DataTable: `cursor_type = "row"`
2. Add columns. **Dynamic column set**:
   - Always: Group, Session, Status, Task
   - Conditionally (when any statusline data exists): Cost, Context, Model
3. Start background workers

### Step 6.4: Background workers

Workers started on mount (added incrementally per implementation order):

**Worker 1: Hyprland event listener** (`@work(exclusive=True)`) — added in Phase 6 first pass
- Creates `HyprlandMonitor`
- Calls `monitor.start()` which connects to event socket
- On session update/remove, posts a custom `SessionChanged` message to the app

**Worker 2: Periodic full refresh** (`set_interval(5.0, self.full_refresh)`) — added in Phase 6 first pass
- Calls `hyprctl clients -j` to catch any missed events
- Also cleans up stale statusline files (no matching Hyprland window)

**Worker 3: Statusline file watcher** (`@work(exclusive=True)`) — added in Phase 6 second pass (after Phase 4)
- Creates `StatuslineWatcher`
- Calls `watcher.watch()`
- On file update, posts a `StatuslineDataChanged` message

### Step 6.5: Table update logic

On `SessionChanged` message:
1. Get all current sessions from `HyprlandMonitor`
2. For each session, merge in statusline data if available (match by `session_name`)
3. Update the DataTable:
   - If row exists (keyed by address): update cells
   - If row is new: add row
   - If row was removed: remove row
4. Sort rows by `workspace_group`

On `StatuslineDataChanged` message:
1. Find matching session by `session_name` (filename stem matches title-parsed session name)
2. Update the session's optional fields (cost, context %, model, etc.)
3. If statusline columns don't exist yet and data is available, add them dynamically
4. Update the affected row's cells

Cell rendering:
- **Group**: plain number
- **Session**: session name string
- **Status**: `⠐` / `✳` / `🔔` with color (green for active, dim for idle, yellow/bold for attention)
- **Task**: truncated task description
- **Cost**: `$X.XX` formatted
- **Context**: bar visualization `████░░ 67%` with color coding (green <70%, yellow 70-90%, red >90%)
- **Model**: display name (e.g., "Opus")

### Step 6.6: Spinner animation

The spinner in the Status column needs to animate. Two approaches:

**Approach A (simpler)**: Use `set_interval(0.96, self.tick_spinners)` to cycle between `⠂` and `⠐` for all ACTIVE sessions. This mirrors Claude Code's own 960ms interval.

**Approach B (event-driven)**: Let the Hyprland event socket drive it — each `windowtitlev2` event already contains the current spinner frame. Just update the cell on each event.

**Choose Approach B** — it's more accurate and doesn't require maintaining separate animation state. The event socket already fires on every spinner frame change.

### Step 6.7: Row selection → workspace switch

```python
def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
    session = self._get_session_for_row(event.row_key)
    if session:
        self.run_worker(self._switch_and_focus(session))

async def _switch_and_focus(self, session: AgentSession) -> None:
    await switch_to_group(session.workspace_group)
    await asyncio.sleep(0.1)  # Brief delay for workspace switch to complete
    await focus_window(session.address)
```

This chains workspace switching with window focusing so the correct terminal gets focus.

### Step 6.8: Header summary

Show a summary line: `"Agent Monitor — 3 active, 1 attention, 2 idle"`

Update on every session change.

### Step 6.9: CSS theme (`monitor.tcss`)

```css
Screen {
    background: $surface;
}

Header {
    dock: top;
}

Footer {
    dock: bottom;
}

DataTable {
    height: 1fr;
}

DataTable > .datatable--cursor {
    background: $accent 30%;
}
```

Keep it minimal — Textual's default theme handles most of it.

---

## Phase 7: Statusline Sidecar Script (`scripts/statusline-sidecar.sh`)

### Step 7.1: Write the script

```bash
#!/bin/bash
input=$(cat)
IDENT="${ZELLIJ_SESSION_NAME:-$(echo "$input" | jq -r '.session_id // "unknown"')}"
# Sanitize IDENT to alphanumeric + hyphen + underscore only
IDENT=$(echo "$IDENT" | tr -cd 'a-zA-Z0-9_-')
if [ -z "$IDENT" ]; then
    IDENT=$(echo "$input" | jq -r '.session_id // empty' | tr -cd 'a-zA-Z0-9_-')
    [ -z "$IDENT" ] && IDENT="unknown"
fi
MONITOR_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/claude-monitor"
mkdir -p "$MONITOR_DIR" && chmod 700 "$MONITOR_DIR"
echo "$input" > "${MONITOR_DIR}/.${IDENT}.tmp"
mv "${MONITOR_DIR}/.${IDENT}.tmp" "${MONITOR_DIR}/${IDENT}.json"
MODEL=$(echo "$input" | jq -r '.model.display_name // "?"')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
COST=$(printf '$%.2f' "$(echo "$input" | jq -r '.cost.total_cost_usd // 0')")
echo "[${MODEL}] ${PCT}% | ${COST}"
```

### Step 7.2: Make executable and test manually

```bash
chmod +x scripts/statusline-sidecar.sh
echo '{"session_id":"test","model":{"display_name":"Opus"},"context_window":{"used_percentage":45},"cost":{"total_cost_usd":1.23}}' | ./scripts/statusline-sidecar.sh
# Should output: [Opus] 45% | $1.23
# Should create $XDG_RUNTIME_DIR/claude-monitor/<session-name>.json
```

---

## Phase 8: Entry Point and Packaging

### Step 8.1: Main entry point

In `app.py`:
```python
def main():
    app = AgentMonitorApp()
    app.run()
```

### Step 8.2: Verify it runs

```bash
python -m agent_monitor.app
# or via the script entry point:
agent-monitor
```

---

## Phase 9: Integration Testing

### Step 9.1: Manual end-to-end test

1. Ensure Claude Code is running in at least one Zellij session
2. Launch `agent-monitor`
3. Verify sessions appear in the table
4. Verify status updates in real-time when Claude Code starts/stops processing
5. Press Enter on a row, verify workspace switches
6. Install statusline sidecar, verify cost/context columns appear

### Step 9.2: Unit tests

- `test_models.py`: Test `parse_window_title()` with all edge cases from Step 2.5
- `test_hyprland.py`:
  - Address normalization (with/without `0x` prefix)
  - Workspace group computation (including group 0 filtering)
  - Event line parsing (windowtitlev2 with commas in title, malformed events)
- `test_statusline.py`:
  - Read valid JSON file
  - Handle malformed JSON gracefully (no crash)
  - Handle file disappearing between detection and read (atomic write race)

### Step 9.3: Startup prerequisite checks

On app startup, verify prerequisites with clear behavior per tool:

| Tool | Required? | Behavior if missing |
|---|---|---|
| `hyprctl` | **Hard requirement** | Exit with error: "hyprctl not found — agent-monitor requires Hyprland" |
| Hyprland event socket | **Hard requirement** | Exit with error: "Cannot find Hyprland event socket" |
| `workspace-group` | **Soft requirement** | Warn on startup; disable workspace switching (Enter key shows "workspace-group not found" notification) |
| `jq` | **Not checked** | Only needed by sidecar script, not the monitor app itself |

---

## Implementation Order

The phases should be implemented in this order, with testable checkpoints:

1. **Phase 1** (scaffolding) — Checkpoint: `pip install -e .` works
2. **Phase 2** (models) — Checkpoint: unit tests pass
3. **Phase 3** (hyprland) — Checkpoint: run standalone script that prints session updates to terminal
4. **Phase 5** (workspace switching) — Checkpoint: can switch groups programmatically
5. **Phase 6** (TUI, steps 6.1-6.5, without statusline worker) — Checkpoint: static table renders with data from hyprctl snapshot
6. **Phase 4** (statusline watcher) — Checkpoint: standalone watcher reads and logs JSON file changes
7. **Phase 6** (steps 6.6-6.9, add statusline worker + StatuslineDataChanged handler) — Checkpoint: live updates work, Enter switches workspace + focuses window, statusline data merges into table
8. **Phase 7** (sidecar script) — Checkpoint: script produces valid JSON files
9. **Phase 8** (packaging) — Checkpoint: `agent-monitor` command works
10. **Phase 9** (integration testing) — Checkpoint: all success criteria met

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Python 3.14t free-threading compatibility with textual/watchfiles | Target >=3.12; user has 3.12 and 3.13 via asdf if needed |
| Hyprland event socket format changes | Pin to observed format; the protocol is stable in Hyprland |
| Statusline JSON schema changes across Claude Code versions | Parse defensively with fallback defaults for all fields |
| Race conditions reading atomic-written JSON files | Already handled: tmp+mv pattern, catch JSONDecodeError |
| Event socket disconnection | Reconnect with backoff, periodic hyprctl refresh as fallback |
