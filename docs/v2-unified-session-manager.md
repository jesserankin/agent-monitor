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
agent-monitor open-run <run-id> --json
agent-monitor set-group <run-or-worktree-id> <group> --json
agent-monitor restore --json
```

Internally, the remote helper can use the same dev-tools, zellij, Hyprland, procfs, Claude, and Codex adapters as the local process.

### Client Adapters

Agent clients are provider-specific sources of status and telemetry. They normalize their own signals into the common `AgentRun` model.

| Client | Baseline signals | Rich signals |
|--------|------------------|--------------|
| **Codex CLI** | process name, cwd, zellij session, `~/.codex/state_5.sqlite` thread metadata | app-server events such as thread status, active flags, turns, token usage |
| **Claude Code** | Hyprland title, process name, cwd, zellij session | statusline sidecar JSON |
| **unknown/custom** | process name, cwd, zellij session | optional monitor JSON written by a wrapper |

Adapters should be optional and independently degradable. If Codex app-server support is unavailable, the Codex adapter still reports running/stopped, cwd, thread title, model, tokens, and last update from process discovery plus SQLite metadata.

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
│  Host        WS  Client  Project          Branch          Status  │
│  ─────────── ─── ─────── ──────────────── ────────────── ───────  │
│  local       [3] codex   game-engine-v2   combat-ui     active    │
│  local       [3] claude  game-engine-v2   feature-auth  idle      │
│  workstation [4] codex   game-engine-v2   npc-dialogue  approval  │
│  cloud-dev   [ ] codex   other-project    api-refactor  stopped   │
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
2. Process tree: terminal PID -> zellij client/server -> agent process
3. Client telemetry keyed by cwd, thread id, or session id
4. Window title as a presentation hint, not the primary identity

For Codex, the baseline identity source is `~/.codex/state_5.sqlite`, especially thread id, cwd, title, source, model, token count, and updated timestamps. For rich live state, prefer Codex app-server events when available. For Claude, the current statusline sidecar remains the rich telemetry source.

### Codex Status Mapping

Codex support should degrade cleanly by source:

| Source | Available status |
|--------|------------------|
| Process + zellij only | `running` or `stopped` |
| SQLite thread metadata | title, cwd, model, token count, last update, but not live blocked/active state |
| App-server `ThreadStatusChanged` | `active`, `idle`, `error`, with `waiting_input` and `waiting_approval` from active flags |
| App-server turn/token events | active turn timing and token/context counters |

The app-server protocol is experimental, so the Codex adapter should treat it as an optional rich backend. The baseline SQLite/process path should be good enough for the TUI to show which worktrees have Codex sessions and when they last moved.

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

Client adapters may need host-visible telemetry files. Claude's statusline sidecar currently writes to `$XDG_RUNTIME_DIR/claude-monitor/`. A client-agnostic wrapper or future custom client should write to `$XDG_RUNTIME_DIR/agent-monitor/<client>/`.

Mount the host's monitor dir into the container so the host agent-monitor can watch it.

Add to `devcontainer.json` mounts:
```json
"source=${localEnv:XDG_RUNTIME_DIR}/agent-monitor,target=/run/user/1000/agent-monitor,type=bind,consistency=cached"
```

Keep the existing Claude mount as a compatibility fallback until the Claude sidecar is migrated.

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

## Current Implementation Status (2026-04-25)

Implemented and manually verified:

- Normalized models exist in `models.py`: `HostSnapshot`, `Worktree`, `AgentRun`, `AgentStatus`, `ClientTelemetry`.
- `registry.py` reads `~/.config/dev_tools/instances.json`, reads/writes `~/.config/agent-monitor/sessions.json`, and merges stopped worktrees with overlay runs.
- `agent-monitor host-snapshot --json` returns a normalized local host snapshot.
- `hosts.py` has a local host adapter with `snapshot`, `set_workspace_group`, and `open_run`. No SSH adapter exists yet.
- The TUI now renders the v2 table columns: `Host`, `WS`, `Client`, `Project`, `Branch`, `Status`, `Task`.
- The TUI still keeps legacy Claude/Hyprland live window rows alongside v2 registry-backed rows.
- Running Codex processes are discovered through `/proc`, matched by CWD to dev-tools worktrees, and shown as `client=codex`, `status=running`.
- Detected Codex runs also capture their ancestor zellij session when available.
- `a` assigns a workspace group for a run and persists it in the agent-monitor overlay.
- `Enter` on a running zellij-backed row first tries to focus an existing Hyprland terminal attached to that zellij session.
- If no existing terminal is found, `Enter` opens a terminal attached to the saved zellij session.
- Fallback terminal creation launches on the middle workspace for the assigned group (`WS 1 -> workspace 11`, `WS 2 -> workspace 12`, etc.) instead of inheriting agent-monitor's floating/shared workspace.
- `Enter` on a row without a saved zellij session creates a stable zellij session name from the run id, persists it to the overlay, and opens it with `zellij attach --create ... options --default-cwd <worktree-cwd>`.
- New zellij session creation can now launch an initial client command. It uses the run's persisted `launch.argv` when present, and defaults to `codex --cd <cwd>` for Codex runs.
- Rows with `client=unknown` and no launch command still open as plain zellij rooted in the worktree.
- Full test suite currently passes: `scripts/test` reports `194 passed`.

Known manual behavior:

- Assigning `extractor::vendor` to `WS 1` persisted across restarts.
- Pressing `Enter` on its running row switches/focuses the existing zellij terminal instead of opening a duplicate.
- Stopping that Codex process and pressing `Enter` reopens/attaches the saved zellij session.
- Creating a zellij session for a Codex row with no saved session now starts Codex in the session before attaching.

Recommended next slices:

1. **Make row identity less synthetic**: distinguish worktree-level default rows from concrete agent-run rows so multiple Codex runs in one worktree can be represented cleanly.
2. **Add Codex SQLite telemetry**: read `~/.codex/state_5.sqlite` for thread title/model/token/updated metadata and attach it to matched runs.
3. **Add explicit CLI helpers**: `agent-monitor set-group`, `agent-monitor open-run`, and eventually remote-safe JSON responses.
4. **Remote support**: add config parsing and SSH host adapter once the local command surface is stable.

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
- [ ] List zellij sessions independently of process discovery
- [x] Inspect terminal/zellij/agent processes where useful
- [x] Read local Hyprland windows when available for zellij window focus
- [x] Expose the same `host-snapshot --json` path used by SSH remotes

### Phase 3: Client Adapters
- Move current Claude title/statusline parsing behind a Claude adapter
- [x] Add baseline Codex process/zellij/CWD discovery
- [ ] Add Codex adapter with baseline SQLite metadata from `~/.codex/state_5.sqlite`
- Add optional Codex app-server event support for rich live status (`active`, `idle`, `waiting_input`, `waiting_approval`, token usage)
- Keep unknown/custom clients discoverable by process name, cwd, zellij session, and optional generic monitor JSON

### Phase 4: SSH Remote Support
- Config file with remote hosts
- SSH-based `agent-monitor host-snapshot --json`
- Remote helper commands for snapshot, open existing run, set-group, and lightweight restore
- Remote zellij attach (opens local terminal with SSH)
- Workspace group inherited from remote overlay — same group on both machines
- `a` key changes pushed back to remote overlay via SSH

### Phase 5: Minimal Run Lifecycle
- [x] Open/focus existing local zellij sessions
- [ ] Open/focus remote zellij sessions
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
| `registry.py` | Implemented | Read dev-tools registry, manage agent-monitor overlay, merge stopped worktrees and baseline Codex processes |
| `hosts.py` | Local only | Host abstraction and local snapshot/open/group commands |
| `zellij.py` | Partial | Session names, terminal attach commands, middle workspace placement |
| `clients/base.py` | Planned | Client adapter protocol and shared status mapping |
| `clients/claude.py` | Planned | Claude title/statusline adapter |
| `clients/codex.py` | Planned | Codex process/SQLite/app-server adapter |
| `devcontainer.py` | Planned | Container status checks used for TUI indicators and open-run capabilities |
| `ssh.py` | Planned | Transport for remote helper commands and remote zellij attach |
| `worktree.py` | Planned | Thin wrapper around `mix dev_tools.create_worktree` / `remove_worktree` via shell or SSH |

## Files to Modify

| File | Status | Change |
|------|--------|--------|
| `app.py` | Partial | Registry-backed v2 rows are present; new/delete/restore and remote display remain |
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
