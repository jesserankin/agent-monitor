"""Tests for zellij terminal attach helpers."""

from unittest.mock import patch

import pytest

from agent_monitor.zellij import (
    attach_session,
    middle_workspace_for_group,
    session_name_for_run_id,
    terminal_attach_command,
    zellij_attach_command,
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
        )


def test_attach_session_launches_terminal_on_middle_workspace():
    with patch("agent_monitor.zellij.terminal_attach_command", return_value=["ghostty", "-e", "zellij", "attach", "s"]), \
         patch("agent_monitor.zellij.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.zellij.subprocess.Popen") as mock_popen:
        assert attach_session("s", workspace_group=1) is True
        mock_popen.assert_called_once_with(
            [
                "hyprctl",
                "dispatch",
                "exec",
                "[workspace 11] ghostty -e zellij attach s",
            ],
            start_new_session=True,
        )


def test_attach_session_returns_false_without_terminal():
    with patch("agent_monitor.zellij.terminal_attach_command", return_value=None), \
         patch("agent_monitor.zellij.subprocess.Popen") as mock_popen:
        assert attach_session("s") is False
        mock_popen.assert_not_called()
