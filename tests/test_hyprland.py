"""Tests for Hyprland integration."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from agent_monitor.hyprland import (
    HyprlandMonitor,
    fetch_clients,
    get_event_socket_path,
    normalize_address,
    parse_event_line,
)
from agent_monitor.models import AgentState


class TestNormalizeAddress:
    """Test address normalization (strip 0x prefix)."""

    def test_with_prefix(self):
        assert normalize_address("0xabc123") == "abc123"

    def test_without_prefix(self):
        assert normalize_address("abc123") == "abc123"

    def test_empty(self):
        assert normalize_address("") == ""

    def test_only_prefix(self):
        assert normalize_address("0x") == ""


class TestParseEventLine:
    """Test Hyprland event line parsing."""

    def test_windowtitlev2(self):
        event = parse_event_line("windowtitlev2>>abc123,my-session | ⠐ Working on stuff")
        assert event == {
            "event": "windowtitlev2",
            "address": "abc123",
            "title": "my-session | ⠐ Working on stuff",
        }

    def test_windowtitlev2_title_with_commas(self):
        event = parse_event_line("windowtitlev2>>abc123,title with, commas, in it")
        assert event == {
            "event": "windowtitlev2",
            "address": "abc123",
            "title": "title with, commas, in it",
        }

    def test_openwindow(self):
        event = parse_event_line(
            "openwindow>>abc123,3,com.mitchellh.ghostty,my-session | ⠐ Working"
        )
        assert event == {
            "event": "openwindow",
            "address": "abc123",
            "workspace_id": 3,
            "window_class": "com.mitchellh.ghostty",
            "title": "my-session | ⠐ Working",
        }

    def test_openwindow_title_with_commas(self):
        event = parse_event_line(
            "openwindow>>abc123,3,com.mitchellh.ghostty,title, with, commas"
        )
        assert event == {
            "event": "openwindow",
            "address": "abc123",
            "workspace_id": 3,
            "window_class": "com.mitchellh.ghostty",
            "title": "title, with, commas",
        }

    def test_closewindow(self):
        event = parse_event_line("closewindow>>abc123")
        assert event == {"event": "closewindow", "address": "abc123"}

    def test_movewindowv2(self):
        event = parse_event_line("movewindowv2>>abc123,5,workspace-5")
        assert event == {
            "event": "movewindowv2",
            "address": "abc123",
            "workspace_id": 5,
            "workspace_name": "workspace-5",
        }

    def test_activewindowv2(self):
        event = parse_event_line("activewindowv2>>abc123")
        assert event == {"event": "activewindowv2", "address": "abc123"}

    def test_activewindowv2_with_hex_prefix(self):
        """activewindowv2 address is raw — normalization happens in handler."""
        event = parse_event_line("activewindowv2>>0xabc123")
        assert event == {"event": "activewindowv2", "address": "0xabc123"}

    def test_unknown_event(self):
        assert parse_event_line("focusedmon>>eDP-1,1") is None

    def test_malformed_no_separator(self):
        assert parse_event_line("garbage data") is None

    def test_malformed_windowtitlev2_no_comma(self):
        assert parse_event_line("windowtitlev2>>no-comma-here") is None

    def test_malformed_openwindow_too_few_parts(self):
        assert parse_event_line("openwindow>>abc123,3") is None

    def test_openwindow_invalid_workspace_id(self):
        assert parse_event_line("openwindow>>abc123,bad,class,title") is None

    def test_movewindowv2_too_few_parts(self):
        assert parse_event_line("movewindowv2>>abc123") is None

    def test_movewindowv2_invalid_workspace_id(self):
        assert parse_event_line("movewindowv2>>abc123,bad,name") is None

    def test_empty_line(self):
        assert parse_event_line("") is None


class TestGetEventSocketPath:
    """Test Hyprland socket path discovery."""

    @patch.dict("os.environ", {"HYPRLAND_INSTANCE_SIGNATURE": "test-sig"})
    @patch("os.path.exists", return_value=True)
    @patch("os.getuid", return_value=1000)
    def test_from_env_var(self, mock_uid, mock_exists):
        path = get_event_socket_path()
        assert path == "/run/user/1000/hypr/test-sig/.socket2.sock"

    @patch.dict("os.environ", {}, clear=True)
    @patch("os.getuid", return_value=1000)
    @patch("os.path.exists", return_value=False)
    @patch("os.listdir", return_value=[])
    def test_no_env_no_dirs_raises(self, mock_listdir, mock_exists, mock_uid):
        with pytest.raises(FileNotFoundError, match="Hyprland event socket"):
            get_event_socket_path()

    @patch.dict("os.environ", {}, clear=True)
    @patch("os.getuid", return_value=1000)
    def test_fallback_scan(self, mock_uid):
        def exists_side_effect(p):
            if p == "/run/user/1000/hypr":
                return True
            return p.endswith(".socket2.sock") and "found-sig" in p

        with patch("os.path.exists", side_effect=exists_side_effect):
            with patch("os.listdir", return_value=["found-sig", "other-dir"]):
                with patch("os.path.isdir", return_value=True):
                    path = get_event_socket_path()
                    assert path == "/run/user/1000/hypr/found-sig/.socket2.sock"


class TestFetchClients:
    """Test hyprctl clients -j fetching."""

    @pytest.mark.asyncio
    async def test_parses_clients(self):
        clients_json = json.dumps([
            {
                "address": "0xabc123",
                "title": "my-session | ⠐ Working on stuff",
                "class": "com.mitchellh.ghostty",
                "workspace": {"id": 3},
                "pid": 1234,
            },
            {
                "address": "0xdef456",
                "title": "Firefox",
                "class": "firefox",
                "workspace": {"id": 1},
                "pid": 5678,
            },
        ])
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (clients_json.encode(), b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_clients()

        assert len(result) == 2
        assert result[0]["address"] == "0xabc123"

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()  # kill() is sync on real Process
        # First call raises TimeoutError, second call (cleanup after kill) succeeds
        mock_proc.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_clients()

        assert result == []
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_on_nonzero_exit(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_clients()

        assert result == []


class TestHyprlandMonitor:
    """Test HyprlandMonitor state management."""

    def _make_monitor(self):
        monitor = HyprlandMonitor.__new__(HyprlandMonitor)
        monitor.sessions = {}
        monitor._window_meta = {}
        monitor._focused_address = None
        monitor.on_session_update = None
        monitor.on_session_remove = None
        monitor._log = MagicMock()
        return monitor

    def test_handle_window_open_claude_session(self):
        monitor = self._make_monitor()
        monitor._handle_window_open({
            "address": "abc123",
            "workspace_id": 3,
            "window_class": "com.mitchellh.ghostty",
            "title": "my-session | ⠐ Working on stuff",
        })
        assert "abc123" in monitor._window_meta
        assert monitor._window_meta["abc123"]["class"] == "com.mitchellh.ghostty"
        assert "abc123" in monitor.sessions
        assert monitor.sessions["abc123"].session_name == "my-session"
        assert monitor.sessions["abc123"].state == AgentState.ACTIVE

    def test_handle_window_open_non_claude(self):
        monitor = self._make_monitor()
        monitor._handle_window_open({
            "address": "abc123",
            "workspace_id": 1,
            "window_class": "firefox",
            "title": "Mozilla Firefox",
        })
        assert "abc123" in monitor._window_meta
        assert "abc123" not in monitor.sessions

    def test_handle_window_open_negative_workspace(self):
        """Negative workspace IDs (special workspaces) should be tracked in meta but not sessions."""
        monitor = self._make_monitor()
        monitor._handle_window_open({
            "address": "abc123",
            "workspace_id": -1,
            "window_class": "com.mitchellh.ghostty",
            "title": "my-session | ⠐ Working",
        })
        assert "abc123" in monitor._window_meta
        assert "abc123" not in monitor.sessions

    def test_handle_window_open_workspace_group_zero(self):
        """Workspace IDs where group == 0 (10, 20, 30) should not create sessions."""
        monitor = self._make_monitor()
        monitor._handle_window_open({
            "address": "abc123",
            "workspace_id": 10,
            "window_class": "com.mitchellh.ghostty",
            "title": "my-session | ⠐ Working",
        })
        assert "abc123" in monitor._window_meta
        assert "abc123" not in monitor.sessions

    def test_handle_window_close(self):
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {"class": "com.mitchellh.ghostty", "workspace_id": 3, "pid": None}
        monitor.sessions["abc123"] = MagicMock()
        monitor._handle_window_close("abc123")
        assert "abc123" not in monitor._window_meta
        assert "abc123" not in monitor.sessions

    def test_handle_window_close_unknown_address(self):
        """Closing an unknown window should not raise."""
        monitor = self._make_monitor()
        monitor._handle_window_close("unknown")  # should not raise

    def test_handle_title_change_new_claude_session(self):
        """Title change on a known window that now matches Claude."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working on stuff")
        assert "abc123" in monitor.sessions
        assert monitor.sessions["abc123"].state == AgentState.ACTIVE
        assert monitor.sessions["abc123"].pid == 1234

    def test_handle_title_change_update_existing(self):
        """Update task description on existing session."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        # First title
        monitor._handle_title_change("abc123", "my-session | ⠐ Working on stuff")
        # Update
        monitor._handle_title_change("abc123", "my-session | ✳ Claude Code")
        assert monitor.sessions["abc123"].state == AgentState.IDLE
        assert monitor.sessions["abc123"].task_description == "Claude Code"

    def test_handle_title_change_keeps_session_on_non_claude_title(self):
        """Title no longer matches Claude → session persists (e.g., Zellij pane switch)."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        assert "abc123" in monitor.sessions

        monitor._handle_title_change("abc123", "jesse@office:~/projects")
        assert "abc123" in monitor.sessions
        # Session retains last known state
        assert monitor.sessions["abc123"].session_name == "my-session"
        assert monitor.sessions["abc123"].state == AgentState.ACTIVE

    def test_handle_title_change_unknown_address(self):
        """Title change for unknown window should be ignored."""
        monitor = self._make_monitor()
        monitor._handle_title_change("unknown", "my-session | ⠐ Working")
        assert "unknown" not in monitor.sessions

    def test_handle_window_move(self):
        """Moving a window updates workspace_id in meta and session."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        assert monitor.sessions["abc123"].workspace_id == 3
        assert monitor.sessions["abc123"].workspace_group == 3

        monitor._handle_window_move("abc123", 15)
        assert monitor._window_meta["abc123"]["workspace_id"] == 15
        assert monitor.sessions["abc123"].workspace_id == 15
        assert monitor.sessions["abc123"].workspace_group == 5

    def test_handle_window_move_to_invalid_group(self):
        """Moving to workspace with group 0 removes session."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        assert "abc123" in monitor.sessions

        monitor._handle_window_move("abc123", 20)
        assert monitor._window_meta["abc123"]["workspace_id"] == 20
        assert "abc123" not in monitor.sessions

    def test_handle_window_move_unknown_address(self):
        """Moving unknown window should not raise."""
        monitor = self._make_monitor()
        monitor._handle_window_move("unknown", 5)

    def test_populate_from_clients(self):
        """Test _populate_from_clients builds both _window_meta and sessions."""
        monitor = self._make_monitor()
        clients = [
            {
                "address": "0xabc123",
                "title": "my-session | ⠐ Working",
                "class": "com.mitchellh.ghostty",
                "workspace": {"id": 3},
                "pid": 1234,
            },
            {
                "address": "0xdef456",
                "title": "Firefox",
                "class": "firefox",
                "workspace": {"id": 1},
                "pid": 5678,
            },
            {
                "address": "0xghi789",
                "title": "other-session | ✳ Claude Code",
                "class": "com.mitchellh.ghostty",
                "workspace": {"id": 12},
                "pid": 9012,
            },
        ]
        monitor._populate_from_clients(clients)
        # All windows in _window_meta
        assert len(monitor._window_meta) == 3
        # Only matching Claude sessions with valid groups
        assert "abc123" in monitor.sessions
        assert "def456" not in monitor.sessions
        assert "ghi789" in monitor.sessions
        assert monitor.sessions["ghi789"].workspace_group == 2

    def test_populate_from_clients_skips_negative_workspace(self):
        monitor = self._make_monitor()
        clients = [
            {
                "address": "0xabc123",
                "title": "my-session | ⠐ Working",
                "class": "com.mitchellh.ghostty",
                "workspace": {"id": -1},
                "pid": 1234,
            },
        ]
        monitor._populate_from_clients(clients)
        assert "abc123" in monitor._window_meta
        assert "abc123" not in monitor.sessions

    def test_populate_from_clients_skips_group_zero(self):
        monitor = self._make_monitor()
        clients = [
            {
                "address": "0xabc123",
                "title": "my-session | ⠐ Working",
                "class": "com.mitchellh.ghostty",
                "workspace": {"id": 10},
                "pid": 1234,
            },
        ]
        monitor._populate_from_clients(clients)
        assert "abc123" in monitor._window_meta
        assert "abc123" not in monitor.sessions


class TestDispatchEventCallbacks:
    """Test that _dispatch_event fires correct callbacks on state transitions."""

    def _make_monitor(self):
        monitor = HyprlandMonitor.__new__(HyprlandMonitor)
        monitor.sessions = {}
        monitor._window_meta = {}
        monitor._focused_address = None
        monitor.on_session_update = None
        monitor.on_session_remove = None
        monitor._log = MagicMock()
        return monitor

    @pytest.mark.asyncio
    async def test_title_change_non_claude_keeps_session_fires_update(self):
        """windowtitlev2 Claude -> non-Claude should keep session and emit on_session_update."""
        updated = []

        async def on_update(session):
            updated.append(session)

        monitor = self._make_monitor()
        monitor.on_session_update = on_update
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        # Create a session first
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        assert "abc123" in monitor.sessions

        # Title change to non-Claude should keep session (Zellij pane switch)
        await monitor._dispatch_event({
            "event": "windowtitlev2",
            "address": "abc123",
            "title": "jesse@office:~/projects",
        })
        assert "abc123" in monitor.sessions
        # Session still fires update since it still exists
        assert len(updated) == 1

    @pytest.mark.asyncio
    async def test_closewindow_non_session_no_callback(self):
        """N1: closewindow for non-session window should NOT fire on_session_remove."""
        removed = []

        async def on_remove(addr):
            removed.append(addr)

        monitor = self._make_monitor()
        monitor.on_session_remove = on_remove
        monitor._window_meta["abc123"] = {
            "class": "firefox",
            "workspace_id": 1,
            "pid": 5678,
        }

        await monitor._dispatch_event({
            "event": "closewindow",
            "address": "abc123",
        })
        assert removed == []

    @pytest.mark.asyncio
    async def test_closewindow_session_fires_remove_callback(self):
        """closewindow for a tracked session should fire on_session_remove."""
        removed = []

        async def on_remove(addr):
            removed.append(addr)

        monitor = self._make_monitor()
        monitor.on_session_remove = on_remove
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        assert "abc123" in monitor.sessions

        await monitor._dispatch_event({
            "event": "closewindow",
            "address": "abc123",
        })
        assert removed == ["abc123"]

    @pytest.mark.asyncio
    async def test_openwindow_claude_fires_update_callback(self):
        """openwindow for a Claude session should fire on_session_update."""
        updated = []

        async def on_update(session):
            updated.append(session)

        monitor = self._make_monitor()
        monitor.on_session_update = on_update

        await monitor._dispatch_event({
            "event": "openwindow",
            "address": "abc123",
            "workspace_id": 3,
            "window_class": "com.mitchellh.ghostty",
            "title": "my-session | ⠐ Working",
        })
        assert len(updated) == 1
        assert updated[0].session_name == "my-session"

    @pytest.mark.asyncio
    async def test_title_update_fires_update_callback(self):
        """Title change on existing session should fire on_session_update."""
        updated = []

        async def on_update(session):
            updated.append(session)

        monitor = self._make_monitor()
        monitor.on_session_update = on_update
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")

        await monitor._dispatch_event({
            "event": "windowtitlev2",
            "address": "abc123",
            "title": "my-session | ✳ Claude Code",
        })
        assert len(updated) == 1
        assert updated[0].state == AgentState.IDLE


class TestFocusTracking:
    """Test activewindowv2 focus change handling."""

    def _make_monitor(self):
        monitor = HyprlandMonitor.__new__(HyprlandMonitor)
        monitor.sessions = {}
        monitor._window_meta = {}
        monitor._focused_address = None
        monitor.on_session_update = None
        monitor.on_session_remove = None
        monitor._log = MagicMock()
        return monitor

    @pytest.mark.asyncio
    async def test_focus_sets_is_focused_on_tracked_session(self):
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")

        await monitor._handle_focus_change("abc123")

        assert monitor.sessions["abc123"].is_focused is True
        assert monitor._focused_address == "abc123"

    @pytest.mark.asyncio
    async def test_focus_clears_old_and_sets_new(self):
        updated = []

        async def on_update(session):
            updated.append((session.address, session.is_focused))

        monitor = self._make_monitor()
        monitor.on_session_update = on_update
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._window_meta["def456"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 4,
            "pid": 5678,
        }
        monitor._handle_title_change("abc123", "session-a | ⠐ Working")
        monitor._handle_title_change("def456", "session-b | ✳ Idle")

        # Focus first session
        await monitor._handle_focus_change("abc123")
        assert monitor.sessions["abc123"].is_focused is True

        updated.clear()

        # Focus second session — first should be cleared
        await monitor._handle_focus_change("def456")
        assert monitor.sessions["abc123"].is_focused is False
        assert monitor.sessions["def456"].is_focused is True
        # Should fire update for old (unfocused) then new (focused)
        assert updated == [("abc123", False), ("def456", True)]

    @pytest.mark.asyncio
    async def test_focus_non_session_window(self):
        """Focusing a non-Claude window should clear focus from old session."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")

        await monitor._handle_focus_change("abc123")
        assert monitor.sessions["abc123"].is_focused is True

        # Focus a non-tracked window
        await monitor._handle_focus_change("unknown999")
        assert monitor.sessions["abc123"].is_focused is False
        assert monitor._focused_address == "unknown999"

    @pytest.mark.asyncio
    async def test_focus_dispatched_via_activewindowv2_event(self):
        """activewindowv2 event should be routed to _handle_focus_change."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")

        await monitor._dispatch_event({
            "event": "activewindowv2",
            "address": "abc123",
        })
        assert monitor.sessions["abc123"].is_focused is True

    @pytest.mark.asyncio
    async def test_focus_with_0x_prefix_normalized(self):
        """Address with 0x prefix should be normalized."""
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1234,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")

        await monitor._handle_focus_change("0xabc123")
        assert monitor.sessions["abc123"].is_focused is True
        assert monitor._focused_address == "abc123"


class TestResolveSessionCwds:
    """Test _resolve_session_cwds process correlation."""

    def _make_monitor(self):
        monitor = HyprlandMonitor.__new__(HyprlandMonitor)
        monitor.sessions = {}
        monitor._window_meta = {}
        monitor._focused_address = None
        monitor.on_session_update = None
        monitor.on_session_remove = None
        monitor._log = MagicMock()
        return monitor

    @patch("agent_monitor.hyprland.find_claude_processes")
    @patch("agent_monitor.hyprland.find_zellij_session_for_terminal")
    @patch("agent_monitor.hyprland._build_zellij_socket_map")
    def test_assigns_cwd_from_procfs(self, mock_socket_map, mock_find_zellij, mock_find_claude):
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1000,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        # Remove the auto-resolved CWD from _populate (called in _handle via _resolve)
        # We need to set up mocks before _resolve_session_cwds is called
        monitor.sessions["abc123"].cwd = None

        mock_socket_map.return_value = {100: "erudite-zebra"}
        mock_find_zellij.return_value = "erudite-zebra"
        mock_find_claude.return_value = [
            {"pid": 2000, "cwd": "/home/user/my-project", "zellij_session_name": "erudite-zebra"},
        ]

        monitor._resolve_session_cwds()

        assert monitor.sessions["abc123"].cwd == "my-project"
        mock_find_zellij.assert_called_once_with(1000, socket_map={100: "erudite-zebra"})

    @patch("agent_monitor.hyprland.find_claude_processes")
    @patch("agent_monitor.hyprland.find_zellij_session_for_terminal")
    @patch("agent_monitor.hyprland._build_zellij_socket_map")
    def test_no_cwd_when_no_zellij(self, mock_socket_map, mock_find_zellij, mock_find_claude):
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": 1000,
        }
        monitor._handle_title_change("abc123", "my-session | ⠐ Working")
        monitor.sessions["abc123"].cwd = None

        mock_socket_map.return_value = {}
        mock_find_zellij.return_value = None

        monitor._resolve_session_cwds()

        assert monitor.sessions["abc123"].cwd is None

    @patch("agent_monitor.hyprland.find_claude_processes")
    @patch("agent_monitor.hyprland.find_zellij_session_for_terminal")
    @patch("agent_monitor.hyprland._build_zellij_socket_map")
    def test_no_cwd_when_no_pid(self, mock_socket_map, mock_find_zellij, mock_find_claude):
        monitor = self._make_monitor()
        monitor._window_meta["abc123"] = {
            "class": "com.mitchellh.ghostty",
            "workspace_id": 3,
            "pid": None,
        }
        monitor._handle_window_open({
            "address": "abc123",
            "workspace_id": 3,
            "window_class": "com.mitchellh.ghostty",
            "title": "my-session | ⠐ Working",
        })
        # openwindow sets pid=None

        mock_socket_map.return_value = {}
        mock_find_zellij.return_value = None

        monitor._resolve_session_cwds()

        assert monitor.sessions["abc123"].cwd is None
        mock_find_zellij.assert_not_called()
