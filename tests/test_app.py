"""Tests for the Agent Monitor TUI application."""

from unittest.mock import patch

import pytest

from agent_monitor.app import (
    AgentMonitorApp,
    SessionChanged,
    SessionRemoved,
    StatuslineDataChanged,
)
from agent_monitor.models import AgentSession, AgentState


def _make_session(
    address="abc123",
    session_name="my-session",
    task_description="Working on stuff",
    state=AgentState.ACTIVE,
    workspace_id=3,
    window_class="com.mitchellh.ghostty",
):
    return AgentSession(
        address=address,
        session_name=session_name,
        task_description=task_description,
        state=state,
        workspace_id=workspace_id,
        window_class=window_class,
    )


@pytest.mark.asyncio
async def test_session_changed_adds_row():
    """Posting SessionChanged should add a row to the DataTable."""
    app = AgentMonitorApp()
    # Patch prerequisites so on_mount doesn't try real Hyprland
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session()
            app.post_message(SessionChanged(session))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.row_count == 1


@pytest.mark.asyncio
async def test_session_changed_updates_existing_row():
    """Posting SessionChanged twice for same address should update, not duplicate."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session()
            app.post_message(SessionChanged(session))
            await pilot.pause()

            updated = _make_session(state=AgentState.IDLE, task_description="Claude Code")
            app.post_message(SessionChanged(updated))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.row_count == 1


@pytest.mark.asyncio
async def test_session_removed_removes_row():
    """Posting SessionRemoved should remove the row from the DataTable."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session()
            app.post_message(SessionChanged(session))
            await pilot.pause()
            assert app.query_one("#sessions").row_count == 1

            app.post_message(SessionRemoved("abc123"))
            await pilot.pause()
            assert app.query_one("#sessions").row_count == 0


@pytest.mark.asyncio
async def test_multiple_sessions_sorted_by_group():
    """Multiple sessions should be sorted by workspace group."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Add session in group 5 first
            s1 = _make_session(address="aaa", session_name="second", workspace_id=5)
            app.post_message(SessionChanged(s1))
            await pilot.pause()

            # Add session in group 2 second
            s2 = _make_session(address="bbb", session_name="first", workspace_id=2)
            app.post_message(SessionChanged(s2))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.row_count == 2
            # First row should be group 2 after sort
            row0 = table.get_row_at(0)
            assert row0[0] == 2


@pytest.mark.asyncio
async def test_subtitle_updates_with_counts():
    """Header subtitle should reflect session state counts."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            assert app.sub_title == "No sessions"

            app.post_message(SessionChanged(_make_session(state=AgentState.ACTIVE)))
            await pilot.pause()
            assert "1 active" in app.sub_title

            app.post_message(SessionChanged(
                _make_session(address="def456", state=AgentState.ATTENTION, workspace_id=4)
            ))
            await pilot.pause()
            assert "1 active" in app.sub_title
            assert "1 attention" in app.sub_title


@pytest.mark.asyncio
async def test_statusline_data_adds_columns():
    """StatuslineDataChanged should add Cost/Context/Model columns dynamically."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Add a session first
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert len(table.columns) == 4  # Group, Session, Status, Task

            # Post statusline data
            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cost_usd": 1.23, "context_used_pct": 45.0, "model_name": "Opus",
                 "duration_ms": 5000, "lines_added": 10, "lines_removed": 5},
            ))
            await pilot.pause()

            assert len(table.columns) == 7  # + Cost, Context, Model


@pytest.mark.asyncio
async def test_statusline_data_merges_into_session():
    """StatuslineDataChanged should update the session's optional fields."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cost_usd": 2.50, "context_used_pct": 67.0, "model_name": "Sonnet",
                 "duration_ms": None, "lines_added": None, "lines_removed": None},
            ))
            await pilot.pause()

            s = app._sessions["abc123"]
            assert s.cost_usd == 2.50
            assert s.context_used_pct == 67.0
            assert s.model_name == "Sonnet"


@pytest.mark.asyncio
async def test_statusline_data_no_matching_session():
    """StatuslineDataChanged with no matching session should be stored for later."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Post statusline data before any session exists
            app.post_message(StatuslineDataChanged(
                "unknown-session",
                {"cost_usd": 1.0, "context_used_pct": 50.0, "model_name": "Opus",
                 "duration_ms": None, "lines_added": None, "lines_removed": None},
            ))
            await pilot.pause()

            # Data should be stored for later matching
            assert "unknown-session" in app._statusline_data


@pytest.mark.asyncio
async def test_statusline_columns_not_duplicated():
    """Posting StatuslineDataChanged multiple times should not duplicate columns."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            data = {"cost_usd": 1.0, "context_used_pct": 50.0, "model_name": "Opus",
                    "duration_ms": None, "lines_added": None, "lines_removed": None}
            app.post_message(StatuslineDataChanged("my-session", data))
            await pilot.pause()
            app.post_message(StatuslineDataChanged("my-session", data))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert len(table.columns) == 7


@pytest.mark.asyncio
async def test_session_changed_merges_pending_statusline():
    """When a session arrives, pending statusline data should be merged."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Statusline data arrives first
            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cost_usd": 3.50, "context_used_pct": 80.0, "model_name": "Opus",
                 "duration_ms": None, "lines_added": None, "lines_removed": None},
            ))
            await pilot.pause()

            # Then the session arrives
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            s = app._sessions["abc123"]
            assert s.cost_usd == 3.50
            assert s.model_name == "Opus"


@pytest.mark.asyncio
async def test_statusline_deletion_clears_session_fields():
    """StatuslineDataChanged with None should clear fields from matching session."""
    app = AgentMonitorApp()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            # Set statusline data
            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cost_usd": 2.50, "context_used_pct": 67.0, "model_name": "Opus",
                 "duration_ms": None, "lines_added": None, "lines_removed": None},
            ))
            await pilot.pause()
            assert app._sessions["abc123"].cost_usd == 2.50

            # Delete statusline data
            app.post_message(StatuslineDataChanged("my-session", None))
            await pilot.pause()

            s = app._sessions["abc123"]
            assert s.cost_usd is None
            assert s.context_used_pct is None
            assert s.model_name is None
