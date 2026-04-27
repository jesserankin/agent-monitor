"""Tests for workspace switching."""

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_monitor.workspace import (
    focus_window,
    focus_window_sync,
    move_window_to_workspace,
    switch_to_group,
    switch_to_group_sync,
    workspace_id_for_group,
)


class TestSwitchToGroup:
    """Test switch_to_group() workspace switching."""

    @pytest.mark.asyncio
    async def test_valid_group(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await switch_to_group(5)

        mock_exec.assert_called_once_with(
            "workspace-group", "5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @pytest.mark.asyncio
    async def test_all_valid_groups(self):
        """Groups 1-9 should all be accepted."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        for group in range(1, 10):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                await switch_to_group(group)  # should not raise

    @pytest.mark.asyncio
    async def test_invalid_group_zero(self):
        with pytest.raises(ValueError, match="1-9"):
            await switch_to_group(0)

    @pytest.mark.asyncio
    async def test_invalid_group_ten(self):
        with pytest.raises(ValueError, match="1-9"):
            await switch_to_group(10)

    @pytest.mark.asyncio
    async def test_invalid_group_negative(self):
        with pytest.raises(ValueError, match="1-9"):
            await switch_to_group(-1)

    @pytest.mark.asyncio
    async def test_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await switch_to_group(3)  # should not raise

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonzero_exit(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"some error")
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await switch_to_group(3)  # should not raise

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("workspace-group not found"),
        ):
            await switch_to_group(3)  # should not raise


class TestFocusWindow:
    """Test focus_window() window focusing."""

    @pytest.mark.asyncio
    async def test_focus_window(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await focus_window("abc123")

        mock_exec.assert_called_once_with(
            "hyprctl", "dispatch", "focuswindow", "address:0xabc123",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @pytest.mark.asyncio
    async def test_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await focus_window("abc123")  # should not raise

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonzero_exit(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await focus_window("abc123")  # should not raise

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("hyprctl not found"),
        ):
            await focus_window("abc123")  # should not raise


def test_switch_to_group_sync():
    with patch("agent_monitor.workspace.subprocess.run") as mock_run:
        assert switch_to_group_sync(4) is True

    mock_run.assert_called_once_with(
        ["workspace-group", "4"],
        capture_output=True,
        check=True,
        timeout=3.0,
    )


def test_switch_to_group_sync_rejects_invalid_group():
    with pytest.raises(ValueError, match="1-9"):
        switch_to_group_sync(10)


def test_switch_to_group_sync_returns_false_on_failure():
    with patch("agent_monitor.workspace.subprocess.run", side_effect=OSError("missing")):
        assert switch_to_group_sync(4) is False


def test_workspace_id_for_group_uses_single_monitor_workspace_block():
    monitors = [
        {
            "name": "eDP-1",
            "activeWorkspace": {"id": 2, "name": "2"},
            "focused": True,
        }
    ]
    completed = subprocess.CompletedProcess(["hyprctl"], 0, stdout=json.dumps(monitors))

    with patch("agent_monitor.workspace.subprocess.run", return_value=completed):
        assert workspace_id_for_group(7) == 7


def test_workspace_id_for_group_preserves_middle_monitor_when_available():
    monitors = [
        {"name": "left", "activeWorkspace": {"id": 4, "name": "4"}},
        {"name": "middle", "activeWorkspace": {"id": 14, "name": "14"}},
        {"name": "right", "activeWorkspace": {"id": 24, "name": "24"}},
    ]
    completed = subprocess.CompletedProcess(["hyprctl"], 0, stdout=json.dumps(monitors))

    with patch("agent_monitor.workspace.subprocess.run", return_value=completed):
        assert workspace_id_for_group(7) == 17


def test_workspace_id_for_group_uses_only_visible_workspace_block():
    monitors = [
        {
            "name": "external",
            "activeWorkspace": {"id": 14, "name": "14"},
            "focused": True,
        }
    ]
    completed = subprocess.CompletedProcess(["hyprctl"], 0, stdout=json.dumps(monitors))

    with patch("agent_monitor.workspace.subprocess.run", return_value=completed):
        assert workspace_id_for_group(7) == 17


def test_workspace_id_for_group_falls_back_to_middle_workspace():
    with patch("agent_monitor.workspace.subprocess.run", side_effect=OSError("missing")):
        assert workspace_id_for_group(7) == 17


def test_workspace_id_for_group_rejects_invalid_group():
    with pytest.raises(ValueError, match="1-9"):
        workspace_id_for_group(10)


def test_move_window_to_workspace():
    with patch("agent_monitor.workspace.subprocess.run") as mock_run:
        assert move_window_to_workspace("abc123", 14) is True

    mock_run.assert_called_once_with(
        ["hyprctl", "dispatch", "movetoworkspacesilent", "14,address:0xabc123"],
        capture_output=True,
        check=True,
        timeout=3.0,
    )


def test_move_window_to_workspace_normalizes_address():
    with patch("agent_monitor.workspace.subprocess.run") as mock_run:
        assert move_window_to_workspace("0xabc123", 14) is True

    assert mock_run.call_args.args[0][-1] == "14,address:0xabc123"


def test_move_window_to_workspace_returns_false_on_failure():
    with patch("agent_monitor.workspace.subprocess.run", side_effect=OSError("missing")):
        assert move_window_to_workspace("abc123", 14) is False


def test_focus_window_sync():
    with patch("agent_monitor.workspace.subprocess.run") as mock_run:
        assert focus_window_sync("abc123") is True

    mock_run.assert_called_once_with(
        ["hyprctl", "dispatch", "focuswindow", "address:0xabc123"],
        capture_output=True,
        check=True,
        timeout=3.0,
    )
