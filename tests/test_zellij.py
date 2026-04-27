"""Tests for zellij terminal attach helpers."""

import subprocess
from unittest.mock import patch

import pytest

from agent_monitor.zellij import (
    attach_session,
    context_used_pct_from_panes,
    create_session_with_command,
    ensure_session,
    list_panes,
    list_sessions,
    middle_workspace_for_group,
    read_context_used_pct_from_pane_titles,
    session_name_for_run_id,
    terminal_attach_command,
    terminal_command,
    zellij_create_background_command,
    zellij_attach_command,
    zellij_list_panes_command,
    zellij_list_sessions_command,
    zellij_run_command,
)


def test_session_name_for_run_id():
    assert session_name_for_run_id("game-engine-v2::combat-ui::main") == "game-engine-v2-combat-ui"


def test_zellij_attach_command_with_create_and_cwd():
    assert zellij_attach_command("game-engine-v2-combat-ui", create=True, cwd="/repo/worktree") == [
        "zellij",
        "attach",
        "--create",
        "game-engine-v2-combat-ui",
        "options",
        "--default-cwd",
        "/repo/worktree",
    ]


def test_zellij_create_background_command_with_cwd():
    assert zellij_create_background_command("my-session", cwd="/repo/worktree") == [
        "zellij",
        "attach",
        "--create-background",
        "my-session",
        "options",
        "--default-cwd",
        "/repo/worktree",
    ]


def test_zellij_run_command_with_cwd_and_pane_name():
    assert zellij_run_command(
        "my-session",
        ["codex", "--cd", "/repo/worktree"],
        cwd="/repo/worktree",
        pane_name="agent",
    ) == [
        "zellij",
        "--session",
        "my-session",
        "run",
        "--name",
        "agent",
        "--cwd",
        "/repo/worktree",
        "--",
        "codex",
        "--cd",
        "/repo/worktree",
    ]


def test_zellij_list_panes_command():
    assert zellij_list_panes_command("my-session") == [
        "zellij",
        "--session",
        "my-session",
        "action",
        "list-panes",
        "--json",
    ]


def test_zellij_list_sessions_command():
    assert zellij_list_sessions_command() == [
        "zellij",
        "list-sessions",
        "--short",
        "--no-formatting",
    ]


def test_list_sessions_parses_short_output():
    completed = subprocess.CompletedProcess(
        ["zellij"],
        0,
        stdout=b"project-a\n\nproject-b\nproject-a\n",
    )
    with patch("agent_monitor.zellij.subprocess.run", return_value=completed) as mock_run:
        assert list_sessions() == ["project-a", "project-b"]

    mock_run.assert_called_once_with(
        zellij_list_sessions_command(),
        capture_output=True,
        check=True,
        timeout=2.0,
    )


def test_list_sessions_returns_empty_when_unavailable():
    with patch("agent_monitor.zellij.subprocess.run", side_effect=FileNotFoundError):
        assert list_sessions() == []


def test_list_panes_parses_json():
    completed = subprocess.CompletedProcess(
        ["zellij"],
        0,
        stdout=b'[{"id":1,"title":"agent-monitor | Context 46% used"}]',
    )
    with patch("agent_monitor.zellij.subprocess.run", return_value=completed) as mock_run:
        assert list_panes("my-session") == [{"id": 1, "title": "agent-monitor | Context 46% used"}]

    mock_run.assert_called_once_with(
        zellij_list_panes_command("my-session"),
        capture_output=True,
        check=True,
        timeout=2.0,
    )


def test_list_panes_returns_empty_when_unavailable():
    with patch("agent_monitor.zellij.subprocess.run", side_effect=FileNotFoundError):
        assert list_panes("my-session") == []


def test_context_used_pct_from_panes_prefers_focused_terminal_title():
    panes = [
        {"is_plugin": False, "is_focused": False, "title": "other | Context 20% used"},
        {"is_plugin": True, "is_focused": True, "title": "plugin | Context 90% used"},
        {"is_plugin": False, "is_focused": True, "title": "agent-monitor | Context 46% used"},
    ]

    assert context_used_pct_from_panes(panes) == 46.0


def test_context_used_pct_from_panes_clamps_value():
    assert context_used_pct_from_panes([{"title": "agent-monitor | Context 146% used"}]) == 100.0


def test_read_context_used_pct_from_pane_titles():
    with patch(
        "agent_monitor.zellij.list_panes",
        return_value=[{"is_plugin": False, "title": "agent-monitor | Context 46% used"}],
    ):
        assert read_context_used_pct_from_pane_titles("my-session") == 46.0


def test_middle_workspace_for_group():
    assert middle_workspace_for_group(1) == 11
    assert middle_workspace_for_group(9) == 19


def test_middle_workspace_for_group_rejects_invalid_group():
    with pytest.raises(ValueError):
        middle_workspace_for_group(0)


def test_terminal_attach_command_for_ghostty():
    assert terminal_attach_command("my-session", terminal="ghostty") == [
        "ghostty",
        "-e",
        "zellij",
        "attach",
        "my-session",
    ]


def test_terminal_command_for_arbitrary_argv():
    assert terminal_command(["ssh", "-t", "host", "zellij", "attach", "s"], terminal="ghostty") == [
        "ghostty",
        "-e",
        "ssh",
        "-t",
        "host",
        "zellij",
        "attach",
        "s",
    ]


def test_terminal_attach_command_with_create_and_cwd():
    assert terminal_attach_command(
        "my-session",
        terminal="ghostty",
        create=True,
        cwd="/repo/worktree",
    ) == [
        "ghostty",
        "-e",
        "zellij",
        "attach",
        "--create",
        "my-session",
        "options",
        "--default-cwd",
        "/repo/worktree",
    ]


def test_terminal_attach_command_for_wezterm():
    assert terminal_attach_command("my-session", terminal="wezterm") == [
        "wezterm",
        "start",
        "--",
        "zellij",
        "attach",
        "my-session",
    ]


def test_terminal_attach_command_uses_first_available_terminal():
    with patch("agent_monitor.zellij.shutil.which", side_effect=lambda name: name == "kitty"):
        assert terminal_attach_command("my-session") == [
            "kitty",
            "zellij",
            "attach",
            "my-session",
        ]


def test_terminal_attach_command_returns_none_when_no_terminal():
    with patch("agent_monitor.zellij.shutil.which", return_value=None):
        assert terminal_attach_command("my-session") is None


def test_attach_session_launches_terminal():
    with patch("agent_monitor.zellij.terminal_attach_command", return_value=["ghostty", "-e", "zellij", "attach", "s"]), \
         patch("agent_monitor.zellij.shutil.which", return_value=None), \
         patch("agent_monitor.zellij.subprocess.Popen") as mock_popen:
        assert attach_session("s") is True
        mock_popen.assert_called_once_with(
            ["ghostty", "-e", "zellij", "attach", "s"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def test_attach_session_launches_terminal_on_middle_workspace():
    with patch("agent_monitor.zellij.terminal_attach_command", return_value=["ghostty", "-e", "zellij", "attach", "s"]), \
         patch("agent_monitor.zellij.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.zellij.workspace_id_for_group", return_value=11) as mock_workspace, \
         patch("agent_monitor.zellij.subprocess.Popen") as mock_popen:
        assert attach_session("s", workspace_group=1) is True
        mock_workspace.assert_called_once_with(1)
        mock_popen.assert_called_once_with(
            [
                "hyprctl",
                "dispatch",
                "exec",
                "[workspace 11] ghostty -e zellij attach s",
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def test_attach_session_creates_session_with_launch_command_then_attaches():
    with patch("agent_monitor.zellij.create_session_with_command", return_value=True) as mock_create, \
         patch("agent_monitor.zellij.terminal_attach_command", return_value=["ghostty", "-e", "zellij", "attach", "s"]) as mock_terminal, \
         patch("agent_monitor.zellij.shutil.which", return_value=None), \
         patch("agent_monitor.zellij.subprocess.Popen") as mock_popen:
        assert attach_session(
            "s",
            create=True,
            cwd="/repo/worktree",
            launch_argv=["codex", "--cd", "/repo/worktree"],
            pane_name="agent",
        ) is True

        mock_create.assert_called_once_with(
            "s",
            ["codex", "--cd", "/repo/worktree"],
            cwd="/repo/worktree",
            pane_name="agent",
        )
        mock_terminal.assert_called_once_with("s", create=False, cwd="/repo/worktree")
        mock_popen.assert_called_once_with(
            ["ghostty", "-e", "zellij", "attach", "s"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def test_attach_session_returns_false_when_launch_command_fails():
    with patch("agent_monitor.zellij.create_session_with_command", return_value=False), \
         patch("agent_monitor.zellij.terminal_attach_command", return_value=["ghostty", "-e", "zellij", "attach", "s"]) as mock_terminal:
        assert attach_session(
            "s",
            create=True,
            cwd="/repo/worktree",
            launch_argv=["codex", "--cd", "/repo/worktree"],
        ) is False
        mock_terminal.assert_called_once_with("s", create=False, cwd="/repo/worktree")


def test_attach_session_checks_terminal_before_launching_command():
    with patch("agent_monitor.zellij.create_session_with_command") as mock_create, \
         patch("agent_monitor.zellij.terminal_attach_command", return_value=None):
        assert attach_session(
            "s",
            create=True,
            cwd="/repo/worktree",
            launch_argv=["codex", "--cd", "/repo/worktree"],
        ) is False
        mock_create.assert_not_called()


def test_create_session_with_command_runs_create_then_command():
    with patch("agent_monitor.zellij.subprocess.run") as mock_run:
        assert create_session_with_command(
            "s",
            ["codex", "--cd", "/repo/worktree"],
            cwd="/repo/worktree",
            pane_name="agent",
        ) is True

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0] == [
        "zellij",
        "attach",
        "--create-background",
        "s",
        "options",
        "--default-cwd",
        "/repo/worktree",
    ]
    assert mock_run.call_args_list[1].args[0] == [
        "zellij",
        "--session",
        "s",
        "run",
        "--name",
        "agent",
        "--cwd",
        "/repo/worktree",
        "--",
        "codex",
        "--cd",
        "/repo/worktree",
    ]


def test_ensure_session_returns_true_when_session_exists():
    with patch("agent_monitor.zellij.list_sessions", return_value=["s"]), \
         patch("agent_monitor.zellij.subprocess.run") as mock_run:
        assert ensure_session("s") is True

    mock_run.assert_not_called()


def test_ensure_session_creates_background_session_without_terminal_attach():
    with patch("agent_monitor.zellij.list_sessions", return_value=[]), \
         patch("agent_monitor.zellij.subprocess.run") as mock_run:
        assert ensure_session("s", cwd="/repo") is True

    mock_run.assert_called_once_with(
        zellij_create_background_command("s", cwd="/repo"),
        capture_output=True,
        check=True,
        timeout=10,
    )


def test_attach_session_returns_false_without_terminal():
    with patch("agent_monitor.zellij.terminal_attach_command", return_value=None), \
         patch("agent_monitor.zellij.subprocess.Popen") as mock_popen:
        assert attach_session("s") is False
        mock_popen.assert_not_called()
