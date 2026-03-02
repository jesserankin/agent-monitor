# Agent Monitor

TUI dashboard for monitoring Claude Code instances across Hyprland workspaces.

Shows all running Claude Code sessions with their state (active/idle/attention), workspace group, task description, context usage, and live duration. The currently focused session is highlighted with a `▸` indicator.

## Setup

### 1. Install

```bash
cd ~/projects/agent-monitor
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### 2. Configure the statusline sidecar

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/home/jesse/projects/agent-monitor/scripts/statusline-sidecar.sh"
  }
}
```

This makes each Claude Code instance write its stats (cost, context usage, model) to `$XDG_RUNTIME_DIR/claude-monitor/` as JSON files, which agent-monitor picks up.

### 3. Run

```bash
.venv/bin/agent-monitor
```

## Requirements

- Hyprland (uses `hyprctl` and the event socket)
- `jq` (used by the sidecar script)
- Zellij (optional — enables CWD resolution for sessions)

## Keybindings

| Key     | Action                                      |
|---------|---------------------------------------------|
| `r`     | Manual refresh                              |
| `Enter` | Switch to the workspace group of selected row |
| `q`     | Quit                                        |

## Running Tests

```bash
scripts/test                              # all tests
scripts/test tests/test_hyprland.py       # specific file
scripts/test tests/test_app.py -v         # verbose
```
