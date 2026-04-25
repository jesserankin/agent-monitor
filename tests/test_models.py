"""Tests for data models and window title parsing."""

from agent_monitor.models import (
    AgentRun,
    AgentState,
    AgentStatus,
    ClientName,
    HostSnapshot,
    Worktree,
    parse_window_title,
)


class TestParseWindowTitle:
    """Test parse_window_title() with various title formats."""

    def test_standard_active(self):
        result = parse_window_title(
            "ge-class | \u2810 Browser Game Implementation", "com.mitchellh.ghostty"
        )
        assert result is not None
        assert result["session_name"] == "ge-class"
        assert result["state"] == AgentState.ACTIVE
        assert result["task_description"] == "Browser Game Implementation"
        assert result["has_attention"] is False

    def test_standard_idle(self):
        result = parse_window_title(
            "iam-catalog | \u2733 Claude Code", "com.mitchellh.ghostty"
        )
        assert result is not None
        assert result["session_name"] == "iam-catalog"
        assert result["state"] == AgentState.IDLE
        assert result["task_description"] == "Claude Code"
        assert result["has_attention"] is False

    def test_attention(self):
        result = parse_window_title(
            "\U0001f514 ge-play-narrative | \u2733 Browser Testing",
            "com.mitchellh.ghostty",
        )
        assert result is not None
        assert result["session_name"] == "ge-play-narrative"
        assert result["state"] == AgentState.ATTENTION
        assert result["task_description"] == "Browser Testing"
        assert result["has_attention"] is True

    def test_attention_active(self):
        result = parse_window_title(
            "\U0001f514 ge-play-narrative | \u2810 Browser Testing",
            "com.mitchellh.ghostty",
        )
        assert result is not None
        assert result["session_name"] == "ge-play-narrative"
        assert result["state"] == AgentState.ATTENTION
        assert result["task_description"] == "Browser Testing"
        assert result["has_attention"] is True

    def test_non_claude_session(self):
        result = parse_window_title(
            "BoK | jesse@office:~/projects/bok", "com.mitchellh.ghostty"
        )
        assert result is None

    def test_no_pipe(self):
        result = parse_window_title("Firefox", "firefox")
        assert result is None

    def test_non_terminal_class(self):
        result = parse_window_title(
            "ge-class | \u2810 Browser Game Implementation", "firefox"
        )
        assert result is None

    def test_attention_empty_session_name(self):
        result = parse_window_title(
            "\U0001f514 | \u2733 Browser Testing", "com.mitchellh.ghostty"
        )
        assert result is None


class TestV2Models:
    def test_worktree_from_devtools_instance_expands_relative_path(self):
        worktree = Worktree.from_devtools_instance(
            "game-engine-v2::combat-ui",
            {
                "branch": "combat-ui",
                "worktree_path": ".worktrees/combat-ui",
                "project_root": "/home/jesse/projects/game-engine-v2",
                "port": "4030",
                "containerized": True,
            },
        )

        assert worktree.project == "game-engine-v2"
        assert worktree.path == "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui"
        assert worktree.port == 4030
        assert worktree.containerized is True

    def test_agent_run_from_overlay_defaults_status_to_stopped(self):
        run = AgentRun.from_dict(
            "game-engine-v2::combat-ui::main",
            {
                "worktree_id": "game-engine-v2::combat-ui",
                "client": "codex",
                "workspace_group": 3,
                "zellij_session": "ge2-combat-ui",
                "cwd": "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui",
                "launch": {"argv": ["codex", "--cd", "{worktree_path}"]},
            },
        )

        assert run.client == ClientName.CODEX
        assert run.status == AgentStatus.STOPPED
        assert run.workspace_group == 3
        assert run.launch["argv"][0] == "codex"

    def test_host_snapshot_round_trip(self):
        snapshot = HostSnapshot.from_dict({
            "host": {"name": "workstation", "transport": "ssh", "hyprland": True},
            "worktrees": [
                {
                    "id": "project::branch",
                    "project": "project",
                    "branch": "branch",
                    "path": "/repo/.worktrees/branch",
                }
            ],
            "agent_runs": [
                {
                    "id": "project::branch::main",
                    "worktree_id": "project::branch",
                    "client": "claude",
                    "status": "idle",
                    "title": "Claude Code",
                    "model": "Sonnet",
                }
            ],
        })

        data = snapshot.to_dict()

        assert data["host"]["name"] == "workstation"
        assert data["agent_runs"][0]["client"] == "claude"
        assert data["agent_runs"][0]["status"] == "idle"
        assert data["agent_runs"][0]["telemetry"]["model"] == "Sonnet"
