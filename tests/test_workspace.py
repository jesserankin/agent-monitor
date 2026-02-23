"""Tests for workspace switching."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_monitor.workspace import focus_window, switch_to_group


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
