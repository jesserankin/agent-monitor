# Agent Monitor v2: Unified Worktree & Session Manager

## Context

Working across multiple worktrees requires manually orchestrating several tools: dev-tools for worktree creation, zellij for terminal sessions, agent-monitor for agent status, Hyprland for workspace assignment, and SSH for remote access. The goal is to extend agent-monitor into a single TUI that manages the full lifecycle: creating worktrees, launching devcontainer sessions, monitoring agent clients such as Codex CLI and Claude Code, assigning workspace groups, and working seamlessly across local and remote machines.

## Core Concepts

### Registry (Split Ownership)

Two registries, each owned by the appropriate tool:

**Dev-tools registry** (`~/.config/dev_tools/instances.json`) — worktree infrastructure:
```json
{
  "instances": {
    "game-engine-v2::combat-ui": {
      "branch": "combat-ui",
      "port": 4030,
      "tidewave_port": 9860,
      "mcp_name": "tidewave-game-engine-v2-combat-ui",
      "worktree_path": ".worktrees/combat-ui",
      "project_root": "/home/jesse/projects/game-engine-v2",
      "containerized": true,
      "created_at": "2026-04-19T..."
    }
  }
}
```

**Agent-monitor overlay** (`~/.config/agent-monitor/sessions.json`) — agent/session/UI concerns:
```json
{
  "agent_runs": {
    "game-engine-v2::combat-ui::main": {
      "worktree_id": "game-engine-v2::combat-ui",
      "client": "codex",
      "workspace_group": 3,
      "zellij_session": "ge2-combat-ui",
      "agent_pane": "agent",
      "cwd": "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui",
      "client_ids": {
        "codex_thread_id": null,
        "claude_session_id": null
      },
      "launch": {
        "argv": ["codex", "--cd", "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui"]
      }
    }
  }
}
```

Agent-monitor reads both and merges the view. Dev-tools handles worktree infrastructure (ports, paths, MCP, devcontainer lifecycle). Agent-monitor handles agent run presentation and control (workspace groups, zellij layout, monitoring, attach/focus behavior).

A worktree can host zero, one, or many agent runs. This matters for Codex because a single worktree can have resumed threads, forks, review runs, and subagents. The TUI should still group rows by worktree, but lifecycle and status belong to the agent run.

See `~/projects/dev-tools/docs/devcontainer-support.md` for the dev-tools design.

### Agent Hosts

Every local or remote machine is modeled as an agent host. A host owns its worktrees, zellij sessions, agent client state, process tree, optional Hyprland state, and agent-monitor overlay.

| Host type | Transport | Responsibilities |
|-----------|-----------|------------------|
| **local** | direct subprocess/filesystem | Read registries, list zellij sessions, inspect processes, read client telemetry, control local Hyprland |
| **ssh** | `ssh host agent-monitor ...` | Return normalized snapshots, execute lifecycle commands, attach/focus remote sessions |

The local TUI should not hard-code remote implementation details such as `sqlite3 ~/.codex/state_5.sqlite` or remote `hyprctl` commands. Instead, remote machines expose a small JSON command surface:

```bash
agent-monitor host-snapshot --json
agent-monitor open-run <run-or-worktree-id> --json
agent-monitor set-group <run-or-worktree-id> <group> --json
agent-monitor codex [--run-name <name>] [-- <codex-args>]
agent-monitor restore --json
```

Internally, the remote helper can use the same dev-tools, zellij, Hyprland, procfs, Claude, and Codex adapters as the local process.

### Client Adapters

Agent clients are provider-specific sources of status and telemetry. They normalize their own signals into the common `AgentRun` model.

| Client | Baseline signals | Rich signals |
|--------|------------------|--------------|
| **Codex CLI** | sidecar status JSON, process name, cwd, zellij session, `~/.codex/state_5.sqlite` thread metadata | app-server events written through the sidecar such as thread status, active flags, turns, token usage |
| **Claude Code** | Hyprland title, process name, cwd, zellij session | statusline sidecar JSON |
| **unknown/custom** | process name, cwd, zellij session | optional monitor JSON written by a wrapper |

Adapters should be optional and independently degradable. Codex status should prefer the agent-monitor sidecar for both host and devcontainer runs. If the sidecar is unavailable, the Codex adapter can still report running/stopped from process discovery and static thread metadata from SQLite.

### Two UI Modes

**Local mode** (Hyprland available): Full TUI with workspace group management, window focusing, live Hyprland event stream.

**Remote/SSH mode**: Same TUI, with worktree and agent ownership deferred to the remote host. Session attach opens a local terminal that SSHes into the owning host and attaches to the remote zellij session. If local Hyprland is available, the local terminal window is moved to the remote run's saved workspace group.

### Agent States

| State | Meaning |
|-------|---------|
| **stopped** | Worktree exists, but no configured agent run is active |
| **running** | Zellij/process exists, but no richer status is available |
| **active** | Agent is currently working on a turn or tool call |
| **idle** | Agent client is open and waiting for a new user turn |
| **waiting_input** | Agent explicitly needs user input |
| **waiting_approval** | Agent is blocked on approval or permission |
| **error** | Agent client reported an error state |
| **unknown** | Discovery found something, but status cannot be classified |

## TUI Layout

```
┌─ Agent Monitor ──────────────────────────────────────────────────┐
│                                                                   │
│  WS  S  Repo                         Port   Ctx             Time  │
│  ─── ─  ──────────────────────────── ─────  ──────────────  ────  │
│  3   ⠂  game-engine-v2/combat-ui     4030   ████░░░░░░ 40%  2m    │
│  3   I  game-engine-v2/feature-auth  9860   █████░░░░░ 50%  6m    │
│  4   W  game-engine-v2/npc-dialogue         ███████░░░ 70%  1m    │
│      S  other-project/api-refactor                                  │
│                                                                   │
│  [n]ew [Enter]open [a]ssign-ws [d]elete [r]estore  [q]uit        │
└───────────────────────────────────────────────────────────────────┘
```

The table shape is already in use for registry-backed rows. During the local-first
phase, Claude windows discovered directly from Hyprland are still rendered as
separate live window rows in the same table. Those rows should eventually be
folded behind the same `AgentRun` identity model instead of remaining a parallel
Claude-specific path.

Current key bindings are intentionally smaller than the end-state design:

| Key | Current behavior | End-state behavior |
|-----|------------------|--------------------|
| `Enter` | Focus existing zellij window or attach/create local zellij session | Same, with remote attach and client launch support |
| `a` | Assign workspace group for registry-backed run | Same for local and remote runs |
| `r` | Refresh snapshot | Restore all sessions |
| `n` | Not implemented | Create worktree and first agent run |
| `d` | Not implemented | Delete worktree and associated runs |

## Key Actions

### `n` — New Worktree

1. Prompt: select project (from dev-tools registry)
2. Prompt: branch name
3. Prompt: select host (local or configured remote)
4. Prompt: select agent client (`codex` by default, `claude` supported)
5. Prompt: workspace group (1-9, or auto)
6. Call dev-tools on the owning host: `mix dev_tools.create_worktree <branch>` (handles container, ports, MCP, direnv)
7. Create zellij session with a client-specific agent pane
8. Register workspace group, zellij session, client, launch command, and worktree association in agent-monitor overlay on the owning host
9. If local Hyprland is available and the session is local, move terminal to assigned workspace group

### `Enter` — Open/Focus Session

- **Running + local Hyprland**: Switch to workspace group, focus window
- **Running + SSH/remote**: Open local terminal with `ssh -t <host> zellij attach <session>`, then move that terminal to the saved workspace group if local Hyprland is available
- **Stopped**: Start zellij session with configured client layout on the owning host, ensure devcontainer is running

### `a` — Assign Workspace Group

- Prompt for group number (1-9)
- Update agent-monitor overlay on whichever host owns the worktree/agent run
- If local Hyprland: move window to that workspace group

### `d` — Delete Worktree

- Confirm prompt
- Kill associated agent zellij sessions/runs on the owning host
- Call dev-tools on the owning host: `mix dev_tools.remove_worktree` (handles git worktree, registry, MCP cleanup)
- Remove associated runs from agent-monitor overlay

### `r` — Restore All

After reboot, for each registered worktree:
1. Ensure devcontainer is running for the project
2. Recreate zellij session with configured client layout and launch command
3. If local Hyprland is available on the owning host: assign to saved workspace group

Until restore exists, `r` remains a refresh key so the v2 snapshot work can be
used and tested without implying lifecycle behavior that is not implemented yet.

## Zellij Session Management

### Layout

A KDL layout template at `~/.config/agent-monitor/layouts/devcontainer.kdl`:

```kdl
layout {
    pane split_direction="vertical" {
        pane size="60%" name="agent"
        pane split_direction="horizontal" {
            pane size="50%" name="server"
            pane size="50%" name="shell"
        }
    }
}
```

### Session Creation

Zellij runs on the owning host. Each pane runs `devcontainer exec ... zsh`, which enters the container when the project is containerized. This keeps zellij sessions visible to `zellij list-sessions` on the host and over SSH.

The agent pane command is supplied by the selected client adapter:

| Client | Agent pane command |
|--------|--------------------|
| `codex` | `codex --cd "$WORKTREE_PATH"` |
| `claude` | `claude --dangerously-skip-permissions` from inside `$WORKTREE_PATH` |
| custom | configured `argv` from the overlay or config |

```bash
# Create session with layout (detached)
zellij attach -b "$SESSION" -l devcontainer

# Agent pane: Codex by default
zellij -s "$SESSION" action write-chars \
  "devcontainer exec --workspace-folder $PROJECT zsh -ic 'codex --cd .worktrees/$BRANCH'\n"

# Focus next pane, write server command
zellij -s "$SESSION" action focus-next-pane
zellij -s "$SESSION" action write-chars \
  "devcontainer exec --workspace-folder $PROJECT zsh -ic 'cd .worktrees/$BRANCH && iex -S mix phx.server'\n"

# Focus next pane, write shell command
zellij -s "$SESSION" action focus-next-pane
zellij -s "$SESSION" action write-chars \
  "devcontainer exec --workspace-folder $PROJECT zsh -ic 'cd .worktrees/$BRANCH'\n"
```

The zsh inside the container loads direnv, which sets `PHX_PORT` and other env vars from the worktree's `.envrc`.

### Client Identity

Agent-monitor should correlate a visible zellij/terminal session to a client-specific identity in this order:

1. Overlay run id and configured zellij session
2. Agent-monitor sidecar status keyed by run id, cwd, thread id, or session id
3. Process tree: terminal PID -> zellij client/server -> agent process
4. Window title as a presentation hint, not the primary identity

For Codex, the primary live identity and status source is `$XDG_RUNTIME_DIR/agent-monitor/runs/<run-id>/status.json`. For static thread metadata, `~/.codex/state_5.sqlite` can still supply thread id, cwd, title, source, model, token count, and updated timestamps. For rich live state, prefer Codex app-server events when available, but publish those events through the sidecar so host, devcontainer, and remote runs all share the same ingestion path. For Claude, the current statusline sidecar remains the rich telemetry source until it is migrated to the generic agent-monitor sidecar.

### Codex Status Mapping

Codex support should degrade cleanly by source:

| Source | Available status |
|--------|------------------|
| Agent-monitor sidecar | `active`, `idle`, `waiting_input`, `waiting_approval`, `error`, plus title/model/tokens/heartbeat |
| Process + zellij only | `running` or `stopped` |
| SQLite thread metadata | title, cwd, model, token count, last update, but not live blocked/active state |
| App-server `ThreadStatusChanged` | Should be translated into sidecar status: `active`, `idle`, `error`, with `waiting_input` and `waiting_approval` from active flags |
| App-server turn/token events | Should be translated into sidecar fields for active turn timing and token/context counters |

The app-server protocol is experimental, so the Codex adapter should treat it as an optional rich backend behind the sidecar. The sidecar schema is the stable contract consumed by the TUI. The baseline process path remains useful only as a fallback to show which worktrees have Codex sessions.

## Devcontainer Integration

### One Container Per Project

Each project with a `.devcontainer/` gets one shared container. All worktrees share the container (shared firewall, shared deps).

```bash
# Check container status
docker ps -qf "label=devcontainer.local_folder=$PROJECT_PATH"

# Start if needed
devcontainer up --workspace-folder "$PROJECT_PATH"
```

### Monitor Telemetry Visibility

Client adapters need host-visible telemetry files. Codex should write a generic agent-monitor sidecar as the primary status channel for all runs:

```text
$XDG_RUNTIME_DIR/agent-monitor/runs/<run-id>/status.json
```

The status file is atomically replaced and has this shape:

```json
{
  "version": 1,
  "run_id": "project::branch::main",
  "worktree_id": "project::branch",
  "client": "codex",
  "status": "waiting_approval",
  "cwd": "/repo/project/.worktrees/branch",
  "zellij_session": "project-branch",
  "thread_id": "thread-123",
  "title": "Implement telemetry",
  "model": "gpt-5.5",
  "tokens_used": 12345,
  "updated_at_ms": 1777160883214,
  "heartbeat_at_ms": 1777160883999
}
```

Claude's statusline sidecar currently writes to `$XDG_RUNTIME_DIR/claude-monitor/`. Keep that compatibility path until Claude is migrated to the generic agent-monitor sidecar.

Mount the host's monitor dir into the container so the host agent-monitor can watch it.

Add to `devcontainer.json` mounts:
```json
"source=${localEnv:XDG_RUNTIME_DIR}/agent-monitor,target=/run/agent-monitor,type=bind,consistency=cached"
```

Inside the devcontainer, launch Codex through a wrapper with `AGENT_MONITOR_RUN_ID` and `AGENT_MONITOR_STATUS_PATH=/run/agent-monitor/runs/<run-id>/status.json`. The wrapper or Codex adapter writes sidecar updates while Codex runs. This makes host and container status ingestion identical.

### Process Tree Resolution

For containerized sessions, prefer overlay identity plus client telemetry over host `/proc` introspection. Host-level process walking can still identify the terminal and zellij session, but agent processes inside containers may not be visible or may have container-specific PIDs.

## SSH / Remote Support

### Architecture

```
Local machine (TUI)                    Remote agent host
┌────────────────────┐                 ┌─────────────────────────┐
│ agent-monitor TUI  │ ──── SSH ────→  │ agent-monitor helper    │
│ local Hyprland     │                 │ dev-tools registry      │
│ local zellij/procs │                 │ zellij/devcontainers    │
│ local client state │                 │ Codex/Claude adapters   │
└────────────────────┘                 │ optional Hyprland       │
                                       └─────────────────────────┘
```

### Remote Discovery

The local TUI asks each remote host for a normalized snapshot instead of reading remote files directly.

```bash
ssh workstation agent-monitor host-snapshot --json
```

Snapshot output contains host metadata, worktrees, agent runs, windows if Hyprland is available, zellij sessions, and client telemetry already normalized by the remote helper.

```json
{
  "host": {"name": "workstation", "transport": "ssh", "hyprland": true},
  "worktrees": [
    {
      "id": "game-engine-v2::combat-ui",
      "project": "game-engine-v2",
      "branch": "combat-ui",
      "path": "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui",
      "containerized": true
    }
  ],
  "agent_runs": [
    {
      "id": "game-engine-v2::combat-ui::main",
      "worktree_id": "game-engine-v2::combat-ui",
      "client": "codex",
      "status": "waiting_approval",
      "workspace_group": 3,
      "zellij_session": "ge2-combat-ui",
      "cwd": "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui",
      "title": "Implement combat UI",
      "model": "gpt-5.5",
      "tokens_used": 412789,
      "updated_at_ms": 1777150331932
    }
  ]
}
```

Attach remains a local terminal operation:

```python
subprocess.Popen(["ghostty", "-e", "ssh", "-t", host, "zellij", "attach", session])
```

The local process can then move that terminal window to the reported workspace group.

### Workspace Group Synchronization

Workspace group is a property of the agent run, with a worktree-level default as fallback. It is stored in the agent-monitor overlay on the machine that owns the worktree. Both local and remote machines use the same 1-9 workspace group convention when Hyprland is available.

**Flow: attaching to a remote session from a local machine**

```
1. Read remote host snapshot:
   → combat-ui Codex run has workspace_group: 3 (assigned on remote)
2. Open terminal:
   → ghostty -e ssh -t host zellij attach ge2-combat-ui
3. Move to local workspace group 3:
   → hyprctl dispatch movetoworkspace 3
4. Result: same worktree on same workspace group on both machines
```

If you're working on `combat-ui` on workspace 3 at the office, then SSH in from home, it appears on workspace 3 at home too.

**Conflict handling**: Local and remote worktrees assigned to the same group share the workspace. Hyprland handles multiple windows per workspace. Reassign via `a` key if isolation is needed.

**Bidirectional sync**: Workspace group changes made from either machine are pushed to the source overlay. Local changes to remote worktrees sync via `ssh host "agent-monitor set-group <run-or-worktree-id> <N> --json"`.

## Configuration

`~/.config/agent-monitor/config.toml`:

```toml
# Remote machines to monitor
[[remotes]]
name = "workstation"
host = "jesse@workstation.local"

[[remotes]]
name = "cloud-dev"
host = "jesse@dev.example.com"

# Agent clients available for new runs
[clients.codex]
enabled = true
default = true
command = ["codex", "--cd", "{worktree_path}"]

[clients.claude]
enabled = true
command = ["claude", "--dangerously-skip-permissions"]

# Default layout for new sessions
[layout]
template = "devcontainer"

# Port ranges for devcontainer projects
[ports]
phoenix_start = 4030
phoenix_end = 4049
tidewave_start = 9860
tidewave_end = 9879
```

## Implementation Phases

The implementation should not wait for the full devcontainer lifecycle to be stable. Build the observation/control architecture first, using plain host paths and existing zellij sessions where possible. Devcontainer startup, restore, and pane launch behavior should plug into the host adapter later as a capability, not define the core model.

## Current Implementation Status (2026-04-26)

Implemented and verified unless noted:

- Normalized models exist in `models.py`: `HostSnapshot`, `Worktree`, `AgentRun`, `AgentStatus`, `ClientTelemetry`.
- `registry.py` reads `~/.config/dev_tools/instances.json`, reads/writes `~/.config/agent-monitor/sessions.json`, and merges overlay runs with sidecar status and baseline Codex process discovery.
- `sidecar.py` reads generic agent-monitor sidecar status files from `$XDG_RUNTIME_DIR/agent-monitor/runs/*/status.json`.
- `agent-monitor codex-sidecar --run-id ... -- <command>` runs Codex behind a wrapper that writes `running` heartbeats and final `stopped`/`error` status.
- `agent-monitor host-snapshot --json` returns a normalized local host snapshot.
- `agent-monitor open-run <run-or-worktree-id> --json` resolves concrete run ids, dev-tools worktree ids, and default `worktree::main` ids, then opens the run through the local host adapter.
- `agent-monitor set-group <run-or-worktree-id> <group> --json` persists workspace group assignment through the same overlay path used by the TUI.
- `agent-monitor codex` is a friendly foreground sidecar wrapper for manual starts. It infers the dev-tools worktree from cwd, defaults to `<worktree-id>::main`, reads `$ZELLIJ_SESSION_NAME`, and runs Codex behind the existing sidecar telemetry writer.
- Codex sidecar telemetry reads explicitly close SQLite connections after each poll, and status-file writes are best-effort so runtime filesystem failures do not kill the wrapped Codex process.
- Cleanly stopped non-dev-tools `agent-monitor codex` runs delete their runtime sidecar status on exit by default. Snapshot reads also prune stopped sidecar-only rows and old sidecar-only errors, while preserving overlay-backed and dev-tools-backed runs.
- Local open/group actions reuse an existing Hyprland terminal attached to the run's zellij session when one is visible. `open-run` switches to the saved workspace group, moves/focuses the existing window, and avoids opening a duplicate attach client; `set-group` moves the existing window to the newly assigned group.
- Runs without a dev-tools worktree still get useful TUI labels from `cwd`: project falls back to the git top-level directory name, and branch falls back to `git branch --show-current` or a short detached HEAD.
- `config.py` reads `~/.config/agent-monitor/config.toml` remote host entries.
- `ssh.py` provides a bounded SSH transport for remote `agent-monitor ... --json`
  commands and local terminal attach helpers for `ssh -t <host> zellij attach`.
- `hosts.py` has local, SSH, and multi-host adapters. The SSH adapter delegates
  `host-snapshot`, `open-run`, and `set-group` to the remote helper instead of
  duplicating registry or sidecar logic locally.
- `zellij.py` lists active zellij session names independently of process discovery, and the local snapshot uses that to mark saved/default zellij-backed runs as `running` when no sidecar/process state is available.
- The TUI now renders compact v2 columns: `WS`, `S`, `Repo`, `Port`, `Ctx`, `Time`. A single local host is shown in the title bar instead of per-row, status is a single-letter/spinner column, and repo labels are `project/branch`.
- Codex sidecar `context_used_pct` renders in `Ctx`; `Time` renders active turn duration for active rows and recent age for idle/waiting rows.
- Recent idle rows are highlighted orange in `WS`, `S`, and `Repo` for ten minutes; stopped rows are dimmed; active rows use an orange spinner instead of a fixed `A`.
- Rows sort assigned workspace groups first, then unassigned non-stopped rows, then stopped rows. Rebuilds preserve the selected row key so periodic refreshes do not snap the cursor back to the first row.
- The TUI still keeps legacy Claude/Hyprland live window rows alongside v2 registry-backed rows.
- Running Codex processes are discovered through `/proc`, matched by CWD to dev-tools worktrees, and shown as `client=codex`, `status=running`.
- Codex sidecar status is merged before process discovery and is treated as primary; process discovery no longer downgrades sidecar states such as `waiting_approval` to `running`.
- Worktree placeholders are now explicit TUI `worktree:<worktree-id>` rows instead of synthetic `AgentRun` rows in the normalized host snapshot. They convert to a default Codex `worktree::main` run only when opening or assigning.
- Concrete agent runs are now explicit TUI `run:<run-id>` rows. Multiple Codex sidecars in the same worktree with distinct run/thread identities survive merge and render as separate rows.
- Detected Codex runs also capture their ancestor zellij session when available.
- `a` assigns a workspace group for a run and persists it in the agent-monitor overlay.
- `Enter` on a running zellij-backed row first tries to focus an existing Hyprland terminal attached to that zellij session.
- `Enter` on a worktree row opens the default run for that worktree. `Enter` on a concrete run row opens that exact run/session.
- If no existing terminal is found, `Enter` opens a terminal attached to the saved zellij session.
- Fallback terminal creation launches on the middle workspace for the assigned group (`WS 1 -> workspace 11`, `WS 2 -> workspace 12`, etc.) instead of inheriting agent-monitor's floating/shared workspace.
- `Enter` on a row without a saved zellij session creates a stable zellij session name from the run id, persists it to the overlay, and opens it with `zellij attach --create ... options --default-cwd <worktree-cwd>`.
- New zellij session creation can now launch an initial client command. It uses the run's persisted `launch.argv` when present, and defaults to `codex --cd <cwd>` for Codex runs. Codex commands are wrapped through `agent-monitor codex-sidecar`.
- When a dev-tools worktree is marked `containerized`, default Codex launch ensures the shared devcontainer is running, keeps zellij and the sidecar on the host, and runs Codex through `devcontainer exec --workspace-folder <project-root> sh -lc 'cd <container-worktree> && exec codex --cd <container-worktree>'`.
- Rows with `client=unknown` and no launch command still open as plain zellij rooted in the worktree.
- `clients/codex.py` reads optional Codex SQLite telemetry from `~/.codex/state_5.sqlite` and `~/.codex/logs_2.sqlite`.
- `agent-monitor codex-sidecar` polls the Codex telemetry reader while the wrapped process is alive and writes richer sidecar statuses when available.
- Codex response events currently map `response.created` / `response.in_progress` to `active`, `response.completed` to `idle`, and best-effort approval/input markers to `waiting_approval` / `waiting_input`. Explicit approval/input wait markers take precedence over generic active sampling markers.
- Codex telemetry includes thread id, title, model, token count, updated timestamp, active turn start, and estimated context percentage when those fields are present.
- Codex sidecar telemetry is keyed by the wrapped process tree's Codex `process_uuid` pid when available, then locks onto the observed thread id so multiple Codex runs in the same cwd do not overwrite each other's title/status.
- When an overlay run already has `client_ids.codex_thread_id`, `LocalHostAdapter` passes it to `agent-monitor codex-sidecar --codex-thread-id ...`; the telemetry reader prefers that expected thread before cwd fallback so resumed same-cwd runs do not drift to a newer unrelated thread.
- The telemetry reader is optional and resilient: missing or locked SQLite files fall back to process-lifecycle `running` heartbeats.
- Full test suite currently passes: `scripts/test` reports `303 passed`.

Known manual behavior:

- Assigning `extractor::vendor` to `WS 1` persisted across restarts.
- Pressing `Enter` on its running row switches/focuses the existing zellij terminal instead of opening a duplicate.
- Stopping that Codex process and pressing `Enter` reopens/attaches the saved zellij session.
- Creating a zellij session for a Codex row with no saved session now starts Codex in the session before attaching.
- A manually wrapped Codex run now resolves to its own thread/title in a same-cwd scenario, moves from `running` to `active`, and settles on `idle` after completion.

Recommended next slices:

1. **Manual SSH verification**: configure a real remote in
   `~/.config/agent-monitor/config.toml`, verify `host-snapshot`, remote
   `open-run`, and remote `set-group` from the TUI, and confirm local SSH
   terminal placement on the saved workspace group.
2. **TUI remote identity polish**: add explicit host labels or host-aware row
   keys if same worktree/run ids can appear on multiple hosts.
3. **Manually verify local CLI lifecycle**: run `agent-monitor open-run ... --json` and `agent-monitor set-group ... --json` against host and devcontainer worktrees as dev-tools container support settles.
4. **Dev-tools handoff**: defer until dev-tools container support stabilizes, then document or print the matching `agent-monitor open-run ...` command after worktree creation.

### Completed Slice: Local Devcontainer Codex Launch

Goal: make local worktree rows start Codex through the sidecar, including
devcontainer worktrees, without moving zellij or sidecar state into the
container.

Implemented points:

- `AgentRun.default_codex_for_worktree` creates a default `worktree::main`
  Codex run from a dev-tools `Worktree`.
- The TUI now converts `worktree:<worktree-id>` rows to default Codex runs at
  open/assign time instead of creating `client=unknown` placeholder runs.
- `LocalHostAdapter.open_run` looks up the run's dev-tools worktree metadata.
- For non-containerized Codex runs with no persisted launch command, new zellij
  sessions launch `agent-monitor codex-sidecar -- codex --cd <host-cwd>`.
- For containerized Codex runs with no persisted launch command, the adapter
  first runs `devcontainer up --workspace-folder <project-root>`, then launches
  a host sidecar that wraps `devcontainer exec`.
- Container path mapping uses `.devcontainer/devcontainer.json`
  `workspaceFolder` when present and defaults to `/workspace`, matching the
  current dev-tools implementation.
- Tests cover default Codex run creation, TUI worktree-row open behavior, host
  launch, devcontainer launch command construction, and devcontainer startup
  failure.

### Completed Slice: Agent Run Identity Cleanup

Goal: keep worktree placeholders and concrete agent runs separate so multiple
Codex runs in one worktree can be represented without being collapsed by merge
fallbacks.

Implemented points:

- `build_host_snapshot` now defaults to returning only concrete agent runs in
  `agent_runs`; stopped worktrees remain represented by `worktrees`.
- The legacy `include_stopped_worktrees=true` path still exists for callers that
  explicitly want synthetic stopped `AgentRun` rows.
- The TUI renders worktrees with no concrete run as `worktree:<worktree-id>` rows.
- The TUI renders concrete runs as `run:<run-id>` rows.
- `Enter` and `a` convert a selected worktree row to a default Codex
  `worktree::main` run at the action boundary, preserving default-run behavior
  without polluting snapshot merge identity.
- Sidecar merging still updates an exact run id match, but no longer falls back
  from a concrete sidecar identity to `worktree_id + client`.
- Sidecar files with distinct run ids or thread ids in the same worktree now
  remain separate runs.
- Tests cover empty worktree rendering, worktree-row open behavior, and two
  same-worktree Codex sidecars with distinct thread ids.

### Completed Slice: Codex Identity Matching

Goal: when a run already has a known Codex thread id, use that identity before
falling back to cwd- or process-log discovery.

Implemented points:

- `agent-monitor codex-sidecar` accepts `--codex-thread-id`.
- `LocalHostAdapter` passes `run.client_ids["codex_thread_id"]` to the sidecar
  when launching a new Codex-backed zellij session.
- `CodexTelemetryReader` already accepted an explicit `thread_id`; the sidecar
  now supplies it.
- The process-scoped Codex log query now includes rows for the expected thread
  id as well as process uuid rows. This lets an expected/resumed thread produce
  status before process correlation is available.
- Tests cover same-cwd newer-thread collisions and expected-thread behavior
  before process-log matching has locked onto a pid.

### Completed Slice: Codex SQLite Live Status Mapping

Goal: wrapped Codex runs should publish richer sidecar state without making the
TUI depend directly on Codex's private SQLite files.

Implemented points:

- Recent `~/.codex/logs_2.sqlite` rows were inspected. The useful structured
  signals are response websocket event types inside `feedback_log_body`,
  especially `response.created`, `response.in_progress`, and
  `response.completed`, plus the turn span's `cwd`, `thread_id`, and `model`.
- `~/.codex/state_5.sqlite` is used for static thread metadata: title, model,
  token count, cwd, thread id, and updated timestamp.
- `clients/codex.py` adds a resilient `CodexTelemetryReader` that returns
  `None` if the SQLite files are missing, locked, or do not contain a matching
  thread.
- `codex_sidecar.py` polls that reader on heartbeats and folds returned fields
  into the existing generic sidecar payload.
- The sidecar passes the wrapped process pid into the reader; the reader expands
  that pid through `/proc/.../children` because the `codex` launcher can spawn
  the real Codex binary as a child. Codex log `process_uuid` values include that
  real process pid, which prevents same-cwd runs from drifting onto newer
  unrelated threads.
- After the first matching live event is observed, the reader locks onto that
  thread id for subsequent metadata/title reads.
- Final `stopped` / `error` lifecycle writes still win when the child process
  exits.
- Tests use fixture SQLite DBs and sidecar fakes so the mapping is deterministic.

Current mappings:

| Codex condition | Sidecar status | Extra fields |
|-----------------|----------------|--------------|
| `response.created` / `response.in_progress` or active turn span | `active` | set `active_since_ms`; update `heartbeat_at_ms` |
| `response.completed` | `idle` | clear `active_since_ms`; update `updated_at_ms` |
| Best-effort approval request/pending marker | `waiting_approval` | clear `active_since_ms` |
| Best-effort user-input marker | `waiting_input` | clear `active_since_ms` |
| Process alive but no rich state | `running` | update `heartbeat_at_ms` only |

Important UI semantics:

- `Time` means active turn duration only. It renders only when `status=active`
  and `active_since_ms` is present.
- `heartbeat_at_ms` should never render as active time; use it only for stale
  sidecar detection or future freshness indicators.
- Process discovery must not downgrade or re-promote an authoritative sidecar
  state. A sidecar-backed `stopped`, `idle`, `waiting_approval`, or
  `waiting_input` row should remain that status even if host `/proc` sees a
  related Codex process.

Known limitations:

- Approval and input waits are currently best-effort because the inspected logs
  did not expose a stable, dedicated wait-state event in the sample set.
- When no process pid or thread id is available, live-log matching falls back to
  recent rows with `cwd=<path>`, so multiple live threads in the same worktree
  can still collide on non-wrapper call paths.
- The reader polls SQLite on sidecar heartbeats. A direct app-server subscription
  may be preferable later if Codex exposes a stable supported protocol.

### Completed Slice: Codex Live Status Manual Verification

Manual verification covered a real wrapped Codex run in the same cwd as another
active Codex session:

- The initial cwd-only mapper drifted onto the wrong same-cwd thread and title.
- Matching by the wrapped process tree's Codex `process_uuid` pid fixed the
  same-cwd title/status collision.
- Neutral process noise and post-completion token-usage rows no longer demote
  an `idle` run back to `running`.
- The manual run moved from `running` to `active` during work and settled on
  `idle` after completion.

### Completed Slice: Local Command Surface

Goal: expose the same local run lifecycle path used by the TUI to scripts,
dev-tools, and future SSH helpers.

Implemented points:

- `agent-monitor open-run <run-or-worktree-id> --json` opens local runs through
  `LocalHostAdapter.open_run`.
- `agent-monitor set-group <run-or-worktree-id> <group> --json` persists
  workspace groups through `LocalHostAdapter.set_workspace_group`.
- Both commands resolve a concrete run id first, then a bare dev-tools worktree
  id to the default Codex `worktree::main` run, preferring an existing overlay
  run when one is already present.
- Both commands also accept an explicit default run id such as
  `<project>::<instance>::main` even before that run exists in the overlay.
- When an attached terminal window already exists for the run's zellij session,
  `open-run` switches to the saved workspace group and moves/focuses that
  window instead of opening another terminal. `set-group` moves it to the newly
  assigned workspace group.
- `open-run --json` includes an `action` field such as `created_session`,
  `opened_terminal`, or `focused_existing_window`.
- `agent-monitor codex` provides the easy manual sidecar path:
  `cd <worktree> && agent-monitor codex`. It infers worktree/run identity,
  picks up `$ZELLIJ_SESSION_NAME`, defaults to `codex --cd <cwd>`, and supports
  named runs with `--run-name`.
- Manual non-dev-tools sidecar rows are ephemeral by default: clean exits remove
  the status file, stopped sidecar-only rows are pruned during snapshot reads,
  and sidecar-only errors expire after a TTL instead of accumulating forever.
- JSON responses use an `ok: true/false` envelope with command, target, resolved
  run payload, and stable error codes for remote callers later.
- Tests cover worktree-id open, existing default-run reuse, default-run-id open,
  group persistence, invalid group JSON errors, unknown target JSON errors, and
  `agent-monitor codex` inference/custom-args behavior.

### Completed Slice: TUI Listing Refinement

Goal: make the TUI listing dense enough for daily use while preserving the
signals needed to identify blocked or active agents quickly.

Implemented points:

- The table now renders `WS`, `S`, `Repo`, `Port`, `Ctx`, and `Time`.
- Single-host local views show the host in the title/subtitle instead of a
  per-row column.
- Project and branch are combined into one `project/branch` repo label.
- Status is compact: waiting is `W`, running is `R`, idle is `I`, stopped is
  `S`, errors are `E`, and active rows use an orange spinner.
- Recent idle rows are highlighted orange in `WS`, `S`, and `Repo` for ten
  minutes so sessions waiting for follow-up stand out.
- Stopped rows are dimmed.
- Port values are shown when dev-tools has a port or Tidewave port, and are
  bold when `127.0.0.1:<port>` is open.
- Rows sort assigned workspace groups first, then unassigned non-stopped rows,
  then stopped rows.
- Rebuilding the table preserves the selected row key so periodic refreshes do
  not snap the cursor back to the first row.

### Completed Slice: Codex Wait-State Hardening

Goal: avoid showing a Codex run as active when the latest observed signal says
it is blocked on approval or user input.

Implemented points:

- Approval/input wait markers now take precedence over generic active sampling
  markers in the Codex log mapper.
- Approval request/pending text maps to `waiting_approval`.
- Pending input markers such as `has_pending_input=true` map to
  `waiting_input`.
- `response.completed` and error response events still win over embedded
  approval/input text so completed turns remain idle/error.
- Tests cover approval precedence over active sampling and pending-input
  mapping.

Remaining caveat:

- The precedence is hardened, but the marker list still needs live verification
  against real wrapped Codex approval/input prompts as Codex log formats evolve.

### Completed Slice: Independent Zellij Session Discovery

Goal: recognize zellij-backed runs even when proc discovery cannot currently
see the agent client process.

Implemented points:

- `zellij.py` now parses `zellij list-sessions --short --no-formatting`.
- `build_host_snapshot` merges active zellij session names before process
  discovery.
- Overlay runs with a saved active zellij session are promoted from
  `stopped`/`unknown` to `running` when no sidecar telemetry says otherwise.
- Worktrees with no concrete run can be represented as the default Codex
  `worktree::main` run when the expected default zellij session exists.
- Sidecar-backed stopped/error/idle/waiting states remain authoritative and are
  not re-promoted by zellij session discovery.
- Tests cover session-list parsing, overlay promotion, default-run promotion,
  and stopped sidecar non-promotion.

### Completed Slice: Remote Host Adapter

Goal: add remote-host observation and control on top of the existing local JSON
command surface, without duplicating registry, sidecar, zellij, workspace-group,
or devcontainer launch logic locally.

Implemented points:

- `config.py` reads `~/.config/agent-monitor/config.toml` and parses
  `[[remotes]]` entries with `name`, `host`, and optional
  `agent_monitor_command`.
- `ssh.py` adds `SshTransport`, which shells out to:
  `ssh <host> agent-monitor <args...>` and parses JSON responses with bounded
  timeouts and explicit transport errors.
- `ssh.py` also adds local terminal attach helpers for:
  `ssh -t <host> zellij attach <session>`, including Hyprland workspace
  placement through the same middle-workspace convention as local attaches.
- `hosts.SshHostAdapter` implements `snapshot`, `open_run`, and
  `set_workspace_group` by calling the remote helper commands:
  `host-snapshot --json`, `open-run <run-id> --json`, and
  `set-group <run-id> <group> --json`.
- Remote snapshots are normalized back into `HostSnapshot`; the configured
  remote name is used as the host name and transport is set to `ssh`.
- `hosts.MultiHostAdapter` merges local plus configured remote snapshots and
  routes `open_run` / `set_workspace_group` back to the owning adapter from the
  latest snapshot.
- The default TUI adapter is now `configured_host_adapter()`: local-only when no
  remotes are configured, local-plus-SSH when remotes exist.
- Remote `open_run` asks the owning host to resolve/open the run through its
  local command surface, then opens a local SSH terminal attach when the returned
  run has a zellij session.
- Tests cover config parsing, SSH command JSON parsing, SSH terminal attach
  command construction, SSH host adapter snapshot/open/group behavior, and
  multi-host action routing.

Known limitations:

- This slice is unit-tested but not manually verified against a real remote.
- The merged TUI still relies on globally unique run/worktree ids. If the same
  ids appear on multiple hosts, host-aware row keys or visible host labels should
  be added before relying on those duplicate rows.
- Remote creation of a brand-new zellij session still depends on the current
  remote `agent-monitor open-run` behavior. A future helper may need a
  non-terminal "ensure session" command so SSH attaches never require a remote
  GUI terminal.

### Resume Here: Manual SSH Verification

The next chat should verify remote support against a real configured host before
starting dev-tools handoff or devcontainer lifecycle work.

Recommended next-chat scope:

1. Add a real `[[remotes]]` entry to `~/.config/agent-monitor/config.toml`.
2. Run `agent-monitor host-snapshot --json` locally and through
   `ssh <host> agent-monitor host-snapshot --json` to compare normalized output.
3. Launch the TUI and confirm local plus remote rows are visible.
4. Press `Enter` on a remote row with an existing zellij session and confirm a
   local terminal opens with `ssh -t <host> zellij attach <session>`.
5. Assign a workspace group with `a` on a remote row and confirm the remote
   overlay changes via `ssh <host> agent-monitor set-group ... --json`.
6. Decide whether duplicate local/remote run ids require host-aware TUI row keys
   before broader daily use.

Fresh-chat startup prompt:

```text
Continue docs/v2-unified-session-manager.md from “Resume Here: Manual SSH Verification”.
Manually verify the new config/SSH host adapter against a real remote. Do not work
on dev-tools handoff or devcontainer lifecycle yet.
```

### DevTools Follow-up Task List

Do not duplicate sidecar, zellij, workspace-group, or launch-command logic in
`../dev-tools`. Dev-tools should remain the worktree/container owner, while
agent-monitor owns the agent-run overlay and launch lifecycle.

Now that `agent-monitor open-run` exists, update dev-tools as a thin handoff:

1. Document the post-create handoff:
   `agent-monitor open-run <project>::<instance>` or
   `agent-monitor open-run <project>::<instance>::main`.
2. Document the id contract:
   dev-tools worktree id is `<project>::<instance>`; agent-monitor default run
   id is `<project>::<instance>::main`.
3. In `mix dev_tools.create_worktree` completion output, optionally detect
   whether `agent-monitor` is available on `PATH` and print the matching
   `agent-monitor open-run ...` command.
4. If `agent-monitor` is unavailable, keep the current fallback guidance:
   `cd <worktree>` and start Codex manually.
   If `agent-monitor` is available but auto-open is disabled, recommend:
   `cd <worktree> && agent-monitor codex`.
5. Later, consider `mix dev_tools.create_worktree <branch> --open-agent`, which
   shells out to `agent-monitor open-run <worktree-id> --json`.
6. Optionally add a project config toggle such as:
   ```toml
   [agent_monitor]
   enabled = true
   auto_open = false
   ```
7. Keep all actual Codex sidecar wrapping in agent-monitor. Dev-tools should not
   construct `agent-monitor codex-sidecar -- ...` directly.

### Completed Slice: Launch Codex on Session Creation

Goal: `Enter` on a stopped Codex run should produce an attached terminal where
Codex is already running in the worktree.

Scope:

- Keep `LocalHostAdapter.open_run` as the single entry point for opening runs.
- Extend the zellij helper layer so session creation can include an initial
  command, while attaching to an existing session remains unchanged.
- Use the run's persisted launch command when present. If absent and
  `client=codex`, default to `["codex", "--cd", run.cwd]`.
- Persist the zellij session before launch, as today, so a partially successful
  open still has stable identity.
- Do not introduce devcontainer launch in this slice. If the worktree is plain
  host-backed today, keep the command plain host-backed.
- Check that a supported terminal can be constructed before creating the zellij
  session and launching the client command.

Behavioral expectations:

- Existing zellij-backed rows keep focusing/attaching without starting a second
  Codex process.
- Newly created sessions open on the assigned middle workspace when a workspace
  group is set.
- Rows with `client=unknown` or no launch command can keep the current plain
  shell behavior.
- Tests should cover command construction without requiring a real zellij or
  terminal process.

Implemented points:

- `zellij.py` now has helpers for background session creation and running an
  initial command inside a session.
- `hosts.LocalHostAdapter.open_run` decides whether it is creating a new session
  and passes an optional launch command down to `zellij.py`.
- `models.AgentRun.stopped_for_worktree` still returns `client=unknown`.
  A default-client config can change this later without altering zellij launch
  mechanics.

### Phase 1: Host Snapshot + Normalized Models
- [x] Introduce `HostSnapshot`, `Worktree`, `AgentRun`, `AgentStatus`, and `ClientTelemetry` models
- [x] Keep reading dev-tools registry (`~/.config/dev_tools/instances.json`)
- [x] Replace worktree-only overlay with agent-run overlay (`~/.config/agent-monitor/sessions.json`)
- [x] TUI shows worktrees from dev-tools registry (stopped state) alongside live agent runs
- [x] Merge dev-tools data + overlay + host discovery + baseline Codex process discovery into one view

### Phase 2: Local Host Adapter
- [x] Read dev-tools registry (`~/.config/dev_tools/instances.json`)
- [x] Read/write agent-monitor overlay
- [x] List zellij sessions independently of process discovery
- [x] Inspect terminal/zellij/agent processes where useful
- [x] Read local Hyprland windows when available for zellij window focus
- [x] Expose the same `host-snapshot --json` path used by SSH remotes
- [x] Expose local `open-run ... --json` and `set-group ... --json` helper commands for scripts and future SSH remotes

### Phase 3: Client Adapters
- Move current Claude title/statusline parsing behind a Claude adapter
- [x] Add generic agent-monitor sidecar reader for client live status
- [x] Add Codex wrapper that writes sidecar heartbeat and exit status
- [x] Add baseline Codex process/zellij/CWD discovery
- [x] Add Codex adapter with baseline SQLite metadata from `~/.codex/state_5.sqlite`
- [x] Add optional Codex log-event support that writes sidecar updates for rich live status (`active`, `idle`, `waiting_input`, `waiting_approval`, token usage)
- [x] Harden approval/input wait mapping so explicit wait markers beat generic active sampling markers
- [ ] Verify approval/input wait mapping against real wrapped Codex runs
- Keep unknown/custom clients discoverable by process name, cwd, zellij session, and optional generic monitor JSON

### Phase 4: SSH Remote Support
- [x] Config file with remote hosts
- [x] SSH-based `agent-monitor host-snapshot --json`
- [x] Remote helper commands for snapshot, open existing run, and set-group
- [ ] Remote helper command for lightweight restore
- [x] Remote zellij attach (opens local terminal with SSH)
- [x] Workspace group inherited from remote overlay — same group on both machines
- [x] `a` key changes pushed back to remote overlay via SSH

### Phase 5: Minimal Run Lifecycle
- [x] Open/focus existing local zellij sessions
- [x] Open/focus remote zellij sessions with local SSH attach for zellij-backed rows
- [x] Create a plain local zellij session for a run with no saved zellij session
- [x] Start a plain client run on the owning host, e.g. `codex --cd <worktree_path>` inside the created session
- [x] Register zellij session metadata in the owning host's overlay
- [x] Agent-monitor manages workspace group assignment on the owning host
- [x] Avoid hard dependencies on devcontainer startup, port allocation, or restore semantics in this phase

### Phase 6: Worktree Lifecycle (via dev-tools)
- `n` key calls `mix dev_tools.create_worktree` on the owning host
- `d` key calls `mix dev_tools.remove_worktree` on the owning host
- Dev-tools remains responsible for git worktree creation/removal, ports, MCP, and container metadata
- Agent-monitor records the selected client, zellij session, launch command, and workspace group in the overlay

### Phase 7: Devcontainer Integration
- Generic monitor telemetry mount for host visibility
- Backward-compatible Claude statusline mount/read path
- Container status in TUI (running/stopped indicator)
- `devcontainer up` on demand when opening a stopped session
- Prefer overlay/client telemetry over procfs for containerized agent identity
- Treat devcontainer launch as a host capability used by `open-run`, not as a prerequisite for status discovery

### Phase 8: Restore & Persistence
- `r` key restores all sessions after reboot
- Ensures containers are running, recreates zellij sessions
- Reassigns workspace groups from overlay
- Handles both local and remote sessions

## Source File Plan

| File | Status | Responsibility |
|------|--------|---------------|
| `models.py` | Implemented | Normalized host/worktree/agent-run/client telemetry models |
| `config.py` | Implemented | Parse local agent-monitor config including remote host definitions |
| `registry.py` | Implemented | Read dev-tools registry, manage agent-monitor overlay, merge sidecar status and baseline Codex processes while keeping stopped worktrees separate by default |
| `sidecar.py` | Implemented | Read generic agent-monitor sidecar status files for Codex/devcontainer/custom runs |
| `hosts.py` | Partial | Local, SSH, and multi-host adapters for snapshot/open/group commands |
| `zellij.py` | Partial | Session names, active session listing, pane metadata, terminal attach commands, middle workspace placement |
| `clients/base.py` | Planned | Client adapter protocol and shared status mapping |
| `clients/claude.py` | Planned | Claude title/statusline adapter |
| `clients/codex.py` | Partial | Optional Codex SQLite metadata and log-event live status reader |
| `devcontainer.py` | Planned | Container status checks used for TUI indicators and open-run capabilities |
| `ssh.py` | Implemented | Transport for remote helper commands and remote zellij attach |
| `worktree.py` | Planned | Thin wrapper around `mix dev_tools.create_worktree` / `remove_worktree` via shell or SSH |

## Files to Modify

| File | Status | Change |
|------|--------|--------|
| `app.py` | Partial | Registry-backed v2 rows, local helper commands, and configured remote adapters are present; new/delete/restore and remote host-label polish remain |
| `hyprland.py` | Partial | Window discovery/focus is used for zellij focusing; Claude-specific parsing still exists |
| `procfs.py` | Partial | Codex process discovery added; client adapters still need to own provider-specific discovery |
| `statusline.py` | Existing | Move Claude-specific extraction into `clients/claude.py`; add generic monitor-file watcher if needed |

## Verification

- Create worktree via TUI → verify git worktree, registry, zellij session, devcontainer
- Open stopped worktree → verify container starts, zellij attaches, selected client launches
- Launch Codex run → verify process/zellij/cwd correlation and thread metadata from `~/.codex/state_5.sqlite`
- Launch Claude run → verify existing title/statusline behavior still works
- Assign workspace group → verify window moves to correct Hyprland workspace
- SSH to remote → verify remote host snapshot includes worktrees, agent runs, zellij sessions, and client telemetry
- Attach remote session → verify local terminal opens on same workspace group
- Change workspace group for remote run/worktree → verify remote overlay updated
- Reboot, restore → verify all sessions recreated on correct workspaces
- Reboot remote, restore from local → verify remote containers and sessions recreated
