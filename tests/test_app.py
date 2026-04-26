"""Tests for the Agent Monitor TUI application."""

from unittest.mock import patch
import subprocess

import pytest
from textual.widgets import DataTable

from agent_monitor.app import (
    AgentMonitorApp,
    SPINNER_FRAMES,
    SessionChanged,
    SessionRemoved,
    StatuslineDataChanged,
    _render_duration,
    _render_context_bar,
    _render_port,
)
from agent_monitor.models import (
    AgentRun,
    AgentSession,
    AgentState,
    AgentStatus,
    ClientName,
    ClientTelemetry,
    HostInfo,
    HostSnapshot,
    Worktree,
)


class StaticHostAdapter:
    def __init__(self, snapshot: HostSnapshot | None = None) -> None:
        self._snapshot = snapshot or HostSnapshot(host=HostInfo(name="local"))
        self.assigned: list[tuple[str, int]] = []
        self.opened: list[str] = []
        self.opened_runs: list[AgentRun] = []
        self.open_result = True

    def snapshot(self) -> HostSnapshot:
        return self._snapshot

    def set_workspace_group(self, run: AgentRun, workspace_group: int) -> AgentRun:
        self.assigned.append((run.id, workspace_group))
        updated = AgentRun(
            id=run.id,
            worktree_id=run.worktree_id,
            client=run.client,
            status=run.status,
            workspace_group=workspace_group,
            zellij_session=run.zellij_session,
            agent_pane=run.agent_pane,
            cwd=run.cwd,
            client_ids=run.client_ids,
            launch=run.launch,
            telemetry=run.telemetry,
        )
        found = False
        self._snapshot.agent_runs = [
            updated if existing.id == run.id else existing
            for existing in self._snapshot.agent_runs
        ]
        for existing in self._snapshot.agent_runs:
            if existing.id == run.id:
                found = True
                break
        if not found:
            self._snapshot.agent_runs.append(updated)
        return updated

    def open_run(self, run: AgentRun) -> bool:
        self.opened.append(run.id)
        self.opened_runs.append(run)
        return self.open_result


def test_render_context_bar_clamps_percentage():
    assert _render_context_bar(125.0).plain == "██████████ 100%"
    assert _render_context_bar(-10.0).plain == "░░░░░░░░░░ 0%"


def test_render_port_marks_open_ports_bold():
    assert _render_port(4030, is_open=True).plain == "4030"
    assert _render_port(4030, is_open=True).style == "bold"
    assert _render_port(4030, is_open=False).style == "dim"


def _make_app(snapshot: HostSnapshot | None = None) -> AgentMonitorApp:
    return AgentMonitorApp(host_adapter=StaticHostAdapter(snapshot))


def _make_snapshot_run(workspace_group: int | None = None, zellij_session: str | None = None) -> tuple[HostSnapshot, AgentRun]:
    run = AgentRun(
        id="game-engine-v2::combat-ui::main",
        worktree_id="game-engine-v2::combat-ui",
        client=ClientName.CODEX,
        workspace_group=workspace_group,
        zellij_session=zellij_session,
        cwd="/repo/game-engine-v2/.worktrees/combat-ui",
    )
    snapshot = HostSnapshot(
        host=HostInfo(name="local"),
        worktrees=[
            Worktree(
                id="game-engine-v2::combat-ui",
                project="game-engine-v2",
                branch="combat-ui",
                path="/repo/game-engine-v2/.worktrees/combat-ui",
            )
        ],
        agent_runs=[run],
    )
    return snapshot, run


def _make_session(
    address="abc123",
    session_name="my-session",
    task_description="Working on stuff",
    state=AgentState.ACTIVE,
    workspace_id=3,
    window_class="com.mitchellh.ghostty",
    cwd=None,
):
    return AgentSession(
        address=address,
        session_name=session_name,
        task_description=task_description,
        state=state,
        workspace_id=workspace_id,
        window_class=window_class,
        cwd=cwd,
    )


@pytest.mark.asyncio
async def test_session_changed_adds_row():
    """Posting SessionChanged should add a row to the DataTable."""
    app = _make_app()
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
async def test_snapshot_worktree_run_shows_on_mount():
    """Stopped registry-backed worktrees should render before any live window exists."""
    snapshot, _ = _make_snapshot_run()
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.row_count == 1
            row = table.get_row_at(0)
            assert row[0].plain == ""
            assert row[1].plain == "S"
            assert row[2].plain == "game-engine-v2/combat-ui"


@pytest.mark.asyncio
async def test_snapshot_run_without_worktree_uses_git_cwd_project_and_branch(tmp_path):
    """Non-dev-tools sidecar runs should still show useful project/branch labels."""
    repo_path = tmp_path / "agent-monitor"
    repo_path.mkdir()
    snapshot = HostSnapshot(
        host=HostInfo(name="local"),
        worktrees=[],
        agent_runs=[
            AgentRun(
                id="agent-monitor",
                worktree_id="agent-monitor",
                client=ClientName.CODEX,
                status=AgentStatus.RUNNING,
                cwd=str(repo_path),
            )
        ],
    )
    app = _make_app(snapshot)

    def fake_run(command, **_kwargs):
        if command[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(command, 0, stdout=f"{repo_path}\n")
        if command[-1] == "--show-current":
            return subprocess.CompletedProcess(command, 0, stdout="main\n")
        raise AssertionError(command)

    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch("agent_monitor.app.subprocess.run", side_effect=fake_run), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            row = table.get_row_at(0)
            assert row[2].plain == "agent-monitor/main"


@pytest.mark.asyncio
async def test_snapshot_worktree_without_run_shows_worktree_row_on_mount():
    """Worktrees without concrete runs should render as worktree rows."""
    snapshot = HostSnapshot(
        host=HostInfo(name="local"),
        worktrees=[
            Worktree(
                id="game-engine-v2::combat-ui",
                project="game-engine-v2",
                branch="combat-ui",
                path="/repo/game-engine-v2/.worktrees/combat-ui",
            )
        ],
        agent_runs=[],
    )
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.row_count == 1
            row = table.get_row_at(0)
            assert row[0] == ""
            assert row[1].plain == "S"
            assert row[2].plain == "game-engine-v2/combat-ui"
            assert app.sub_title == "local · 1 stopped"


@pytest.mark.asyncio
async def test_snapshot_multiple_runs_for_same_worktree_render_distinct_rows():
    """Concrete runs in the same worktree should not collapse in the table."""
    worktree = Worktree(
        id="game-engine-v2::combat-ui",
        project="game-engine-v2",
        branch="combat-ui",
        path="/repo/game-engine-v2/.worktrees/combat-ui",
    )
    snapshot = HostSnapshot(
        host=HostInfo(name="local"),
        worktrees=[worktree],
        agent_runs=[
            AgentRun(
                id="game-engine-v2::combat-ui::main",
                worktree_id=worktree.id,
                client=ClientName.CODEX,
                telemetry=ClientTelemetry(title="Main task"),
            ),
            AgentRun(
                id="game-engine-v2::combat-ui::review",
                worktree_id=worktree.id,
                client=ClientName.CODEX,
                telemetry=ClientTelemetry(title="Review task"),
            ),
        ],
    )
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.row_count == 2
            repos = {table.get_row_at(index)[2].plain for index in range(table.row_count)}
            assert repos == {"game-engine-v2/combat-ui"}
            assert not app._worktree_rows


@pytest.mark.asyncio
async def test_snapshot_refresh_preserves_selected_row():
    """Periodic snapshot refreshes should not snap the cursor back to the first row."""
    snapshot = HostSnapshot(
        host=HostInfo(name="local"),
        worktrees=[
            Worktree(
                id="game-engine-v2::combat-ui",
                project="game-engine-v2",
                branch="combat-ui",
                path="/repo/game-engine-v2/.worktrees/combat-ui",
            ),
            Worktree(
                id="game-engine-v2::save-system",
                project="game-engine-v2",
                branch="save-system",
                path="/repo/game-engine-v2/.worktrees/save-system",
            ),
        ],
        agent_runs=[
            AgentRun(
                id="game-engine-v2::combat-ui::main",
                worktree_id="game-engine-v2::combat-ui",
                client=ClientName.CODEX,
            ),
            AgentRun(
                id="game-engine-v2::save-system::main",
                worktree_id="game-engine-v2::save-system",
                client=ClientName.CODEX,
            ),
        ],
    )
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            table.move_cursor(row=1, scroll=False)
            selected_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)

            app._refresh_snapshot_rows()
            await pilot.pause()

            refreshed_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            assert table.cursor_row == 1
            assert refreshed_key == selected_key


@pytest.mark.asyncio
async def test_snapshot_run_with_telemetry_shows_context_and_time_columns():
    """Registry-backed runs should render sidecar telemetry columns."""
    snapshot, run = _make_snapshot_run()
    run.status = AgentStatus.ACTIVE
    run.telemetry = ClientTelemetry(
        title="Sidecar task",
        context_used_pct=67.0,
        active_since_ms=1_000_000,
    )
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch("agent_monitor.app.time.time", return_value=1_125), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            assert len(table.columns) == 6
            row = table.get_row_at(0)
            assert row[1].plain == SPINNER_FRAMES[0]
            assert row[4].plain == "███████░░░ 67%"
            assert row[5].plain == "2m"


@pytest.mark.asyncio
async def test_active_snapshot_run_status_spins():
    snapshot, run = _make_snapshot_run()
    run.status = AgentStatus.ACTIVE
    run.telemetry = ClientTelemetry(active_since_ms=1_000_000)
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch("agent_monitor.app.time.time", return_value=1_125), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            assert table.get_row_at(0)[1].plain == SPINNER_FRAMES[0]

            app._tick_spinners()

            assert table.get_row_at(0)[1].plain == SPINNER_FRAMES[1]
            assert table.get_row_at(0)[5].plain == "2m"


@pytest.mark.asyncio
async def test_snapshot_running_run_does_not_show_heartbeat_age_as_time():
    snapshot, run = _make_snapshot_run()
    run.status = AgentStatus.RUNNING
    run.telemetry = ClientTelemetry(heartbeat_at_ms=1_000_000)
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch("agent_monitor.app.time.time", return_value=1_030), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            assert len(table.columns) == 6


@pytest.mark.asyncio
async def test_snapshot_terminal_status_does_not_show_running_time():
    snapshot, run = _make_snapshot_run()
    run.status = AgentStatus.ERROR
    run.telemetry = ClientTelemetry(
        context_used_pct=67.0,
        active_since_ms=1_000_000,
        heartbeat_at_ms=1_030_000,
    )
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch("agent_monitor.app.time.time", return_value=1_125), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions")
            row = table.get_row_at(0)
            assert row[5].plain == ""


@pytest.mark.asyncio
async def test_recent_idle_run_highlights_workspace_and_repo():
    snapshot, run = _make_snapshot_run(workspace_group=4)
    run.status = AgentStatus.IDLE
    run.telemetry = ClientTelemetry(updated_at_ms=1_000_000)
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch("agent_monitor.app.time.time", return_value=1_300), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            row = app.query_one("#sessions").get_row_at(0)
            assert row[0].plain == "4"
            assert row[0].style == "dark_orange"
            assert row[1].style == "dark_orange"
            assert row[2].plain == "game-engine-v2/combat-ui"
            assert row[2].style == "dark_orange"


@pytest.mark.asyncio
async def test_assign_run_workspace_group_updates_snapshot_row():
    """Assigning a group should persist through the host adapter and refresh the row."""
    snapshot, run = _make_snapshot_run()
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            await pilot.pause()

            app._assign_run_workspace_group(run, 6)
            await pilot.pause()

            table = app.query_one("#sessions")
            row = table.get_row_at(0)
            assert row[0].plain == "6"
            assert app._host_adapter.assigned == [("game-engine-v2::combat-ui::main", 6)]


@pytest.mark.asyncio
async def test_open_run_switches_workspace_and_attaches_zellij():
    snapshot, run = _make_snapshot_run(workspace_group=4, zellij_session="ge-combat-ui")
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value=None), \
         patch("agent_monitor.app.switch_to_group") as mock_switch:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_switch.assert_called_once_with(4)
            assert app._host_adapter.opened == ["game-engine-v2::combat-ui::main"]
            assert app._host_adapter.opened_runs[0].client == ClientName.CODEX


@pytest.mark.asyncio
async def test_open_run_focuses_existing_zellij_window_without_attaching():
    snapshot, run = _make_snapshot_run(workspace_group=4, zellij_session="ge-combat-ui")
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value={"address": "abc123", "workspace_id": 14}), \
         patch("agent_monitor.app.switch_to_group") as mock_switch, \
         patch("agent_monitor.app.move_window_to_workspace") as mock_move, \
         patch("agent_monitor.app.focus_window") as mock_focus:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_switch.assert_called_once_with(4)
            mock_move.assert_called_once_with("abc123", 14)
            mock_focus.assert_called_once_with("abc123")
            assert app._host_adapter.opened == []


@pytest.mark.asyncio
async def test_open_run_uses_existing_window_workspace_when_run_has_no_group():
    snapshot, run = _make_snapshot_run(zellij_session="ge-combat-ui")
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value={"address": "abc123", "workspace_id": 14}), \
         patch("agent_monitor.app.switch_to_group") as mock_switch, \
         patch("agent_monitor.app.move_window_to_workspace") as mock_move, \
         patch("agent_monitor.app.focus_window") as mock_focus:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_switch.assert_called_once_with(4)
            mock_move.assert_called_once_with("abc123", 14)
            mock_focus.assert_called_once_with("abc123")
            assert app._host_adapter.opened == []


@pytest.mark.asyncio
async def test_open_run_focuses_live_session_with_matching_cwd_when_saved_zellij_is_stale():
    snapshot, run = _make_snapshot_run(workspace_group=7, zellij_session="ge-grid")
    run.cwd = "/home/jesse/projects/game-engine-v2-play_testing"
    app = _make_app(snapshot)
    live_session = _make_session(
        address="abc123",
        session_name="ge-comp",
        task_description="game-engine-v2-play_testing",
        state=AgentState.IDLE,
        workspace_id=17,
        cwd="game-engine-v2-play_testing",
    )
    app._sessions[live_session.address] = live_session

    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value=None), \
         patch("agent_monitor.app.switch_to_group") as mock_switch, \
         patch("agent_monitor.app.move_window_to_workspace") as mock_move, \
         patch("agent_monitor.app.focus_window") as mock_focus:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_switch.assert_called_once_with(7)
            mock_move.assert_called_once_with("abc123", 17)
            mock_focus.assert_called_once_with("abc123")
            assert app._host_adapter.opened == []


@pytest.mark.asyncio
async def test_open_run_focuses_live_session_with_truncated_cwd_in_title():
    snapshot, run = _make_snapshot_run(workspace_group=7, zellij_session="ge-grid")
    run.cwd = "/home/jesse/projects/game-engine-v2-play_testing"
    app = _make_app(snapshot)
    live_session = _make_session(
        address="abc123",
        session_name="ge-comp",
        task_description="... game-engine-v2-play_t...",
        state=AgentState.IDLE,
        workspace_id=17,
    )
    app._sessions[live_session.address] = live_session

    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value=None), \
         patch("agent_monitor.app.switch_to_group"), \
         patch("agent_monitor.app.move_window_to_workspace"), \
         patch("agent_monitor.app.focus_window") as mock_focus:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_focus.assert_called_once_with("abc123")
            assert app._host_adapter.opened == []


@pytest.mark.asyncio
async def test_data_table_row_selected_opens_run():
    snapshot, _ = _make_snapshot_run(workspace_group=4, zellij_session="ge-combat-ui")
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value=None), \
         patch("agent_monitor.app.switch_to_group"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions", DataTable)
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            event = DataTable.RowSelected(table, 0, row_key)
            app.on_data_table_row_selected(event)
            await pilot.pause(0.3)

            assert app._host_adapter.opened == ["game-engine-v2::combat-ui::main"]
            assert app._host_adapter.opened_runs[0].client == ClientName.CODEX


@pytest.mark.asyncio
async def test_data_table_row_selected_opens_worktree_default_run():
    snapshot = HostSnapshot(
        host=HostInfo(name="local"),
        worktrees=[
            Worktree(
                id="game-engine-v2::combat-ui",
                project="game-engine-v2",
                branch="combat-ui",
                path="/repo/game-engine-v2/.worktrees/combat-ui",
            )
        ],
        agent_runs=[],
    )
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value=None), \
         patch("agent_monitor.app.switch_to_group"):
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#sessions", DataTable)
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            event = DataTable.RowSelected(table, 0, row_key)
            app.on_data_table_row_selected(event)
            await pilot.pause(0.3)

            assert app._host_adapter.opened == ["game-engine-v2::combat-ui::main"]


@pytest.mark.asyncio
async def test_open_run_without_workspace_group_still_attaches_zellij():
    snapshot, run = _make_snapshot_run(zellij_session="ge-combat-ui")
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window", return_value=None), \
         patch("agent_monitor.app.switch_to_group") as mock_switch:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_switch.assert_not_called()
            assert app._host_adapter.opened == ["game-engine-v2::combat-ui::main"]


@pytest.mark.asyncio
async def test_open_run_without_zellij_session_creates_session():
    snapshot, run = _make_snapshot_run(workspace_group=4)
    app = _make_app(snapshot)
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"), \
         patch("agent_monitor.app.find_zellij_window") as mock_find_window, \
         patch("agent_monitor.app.switch_to_group") as mock_switch:
        async with app.run_test() as pilot:
            await pilot.pause()

            await app._open_run(run)

            mock_find_window.assert_not_called()
            mock_switch.assert_called_once_with(4)
            assert app._host_adapter.opened == ["game-engine-v2::combat-ui::main"]


@pytest.mark.asyncio
async def test_session_changed_updates_existing_row():
    """Posting SessionChanged twice for same address should update, not duplicate."""
    app = _make_app()
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
    app = _make_app()
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
    app = _make_app()
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
            assert row0[0] == "2"


@pytest.mark.asyncio
async def test_subtitle_updates_with_counts():
    """Header subtitle should reflect session state counts."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            assert app.sub_title == "local"

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
    """StatuslineDataChanged should add Context and Time columns dynamically."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Add a session first
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert len(table.columns) == 6

            # Post statusline data
            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cwd": None, "cost_usd": 1.23, "context_used_pct": 45.0, "model_name": "Opus",
                 "duration_ms": 5000, "lines_added": 10, "lines_removed": 5},
            ))
            await pilot.pause()

            assert len(table.columns) == 6


@pytest.mark.asyncio
async def test_statusline_data_merges_into_session():
    """StatuslineDataChanged should update the session's optional fields."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cwd": None, "cost_usd": 2.50, "context_used_pct": 67.0, "model_name": "Sonnet",
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
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Post statusline data before any session exists
            app.post_message(StatuslineDataChanged(
                "unknown-session",
                {"cwd": None, "cost_usd": 1.0, "context_used_pct": 50.0, "model_name": "Opus",
                 "duration_ms": None, "lines_added": None, "lines_removed": None},
            ))
            await pilot.pause()

            # Data should be stored for later matching
            assert "unknown-session" in app._statusline_data


@pytest.mark.asyncio
async def test_statusline_columns_not_duplicated():
    """Posting StatuslineDataChanged multiple times should not duplicate columns."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(session_name="my-session")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            data = {"cwd": None, "cost_usd": 1.0, "context_used_pct": 50.0, "model_name": "Opus",
                    "duration_ms": None, "lines_added": None, "lines_removed": None}
            app.post_message(StatuslineDataChanged("my-session", data))
            await pilot.pause()
            app.post_message(StatuslineDataChanged("my-session", data))
            await pilot.pause()

            table = app.query_one("#sessions")
            assert len(table.columns) == 6


@pytest.mark.asyncio
async def test_session_changed_merges_pending_statusline():
    """When a session arrives, pending statusline data should be merged."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Statusline data arrives first
            app.post_message(StatuslineDataChanged(
                "my-session",
                {"cwd": None, "cost_usd": 3.50, "context_used_pct": 80.0, "model_name": "Opus",
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
    app = _make_app()
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
                {"cwd": None, "cost_usd": 2.50, "context_used_pct": 67.0, "model_name": "Opus",
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


class TestRenderDuration:
    """Test the _render_duration helper."""

    def test_seconds(self):
        assert _render_duration(45000) == "45s"

    def test_minutes(self):
        assert _render_duration(780000) == "13m"

    def test_hours_and_minutes(self):
        assert _render_duration(3900000) == "1h 5m"

    def test_exact_hours(self):
        assert _render_duration(7200000) == "2h"

    def test_zero(self):
        assert _render_duration(0) == "0s"

    def test_under_one_second(self):
        assert _render_duration(500) == "0s"


@pytest.mark.asyncio
async def test_statusline_matches_by_cwd():
    """StatuslineDataChanged should match session by CWD when available."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Session with CWD set (name won't match statusline key)
            session = _make_session(session_name="ge-play-narrative", cwd="game-engine-v2-play-narrative")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            # Statusline data keyed by CWD basename (from sidecar)
            app.post_message(StatuslineDataChanged(
                "game-engine-v2-play-narrative",
                {"cwd": "/home/user/game-engine-v2-play-narrative", "cost_usd": 5.0,
                 "context_used_pct": 30.0, "model_name": "Opus",
                 "duration_ms": 60000, "lines_added": 50, "lines_removed": 10},
            ))
            await pilot.pause()

            s = app._sessions["abc123"]
            assert s.cost_usd == 5.0
            assert s.context_used_pct == 30.0


@pytest.mark.asyncio
async def test_session_changed_merges_pending_by_cwd():
    """When a session with CWD arrives, pending statusline data should match by CWD."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            # Statusline arrives first, keyed by CWD basename
            app.post_message(StatuslineDataChanged(
                "my-project",
                {"cwd": "/home/user/my-project", "cost_usd": 7.0,
                 "context_used_pct": 55.0, "model_name": "Sonnet",
                 "duration_ms": 120000, "lines_added": 100, "lines_removed": 20},
            ))
            await pilot.pause()

            # Then session arrives with CWD
            session = _make_session(session_name="some-tab-name", cwd="my-project")
            app.post_message(SessionChanged(session))
            await pilot.pause()

            s = app._sessions["abc123"]
            assert s.cost_usd == 7.0
            assert s.model_name == "Sonnet"


@pytest.mark.asyncio
async def test_active_since_set_on_active():
    """active_since should be set when session becomes ACTIVE."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(state=AgentState.ACTIVE)
            app.post_message(SessionChanged(session))
            await pilot.pause()

            s = app._sessions["abc123"]
            assert s.active_since is not None


@pytest.mark.asyncio
async def test_active_since_cleared_on_idle():
    """active_since should be None when session goes IDLE."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(state=AgentState.ACTIVE)
            app.post_message(SessionChanged(session))
            await pilot.pause()
            assert app._sessions["abc123"].active_since is not None

            idle = _make_session(state=AgentState.IDLE)
            app.post_message(SessionChanged(idle))
            await pilot.pause()
            assert app._sessions["abc123"].active_since is None


@pytest.mark.asyncio
async def test_active_since_preserved_across_updates():
    """active_since should not reset when ACTIVE session gets updated."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(state=AgentState.ACTIVE)
            app.post_message(SessionChanged(session))
            await pilot.pause()

            original_since = app._sessions["abc123"].active_since

            updated = _make_session(state=AgentState.ACTIVE, task_description="New task")
            app.post_message(SessionChanged(updated))
            await pilot.pause()

            assert app._sessions["abc123"].active_since == original_since


@pytest.mark.asyncio
async def test_focused_session_has_indicator():
    """Focused session should render with ▸ prefix and bold name."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(state=AgentState.IDLE)
            session.is_focused = True
            app.post_message(SessionChanged(session))
            await pilot.pause()

            table = app.query_one("#sessions")
            row = table.get_row_at(0)
            name_cell = row[2]
            assert name_cell.plain.startswith("\u25b8 ")
            assert "bold" in str(name_cell.style)


@pytest.mark.asyncio
async def test_unfocused_session_no_indicator():
    """Unfocused session should not have ▸ prefix."""
    app = _make_app()
    with patch("agent_monitor.app.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.app.get_event_socket_path", return_value="/fake/socket"), \
         patch.object(app, "_start_monitor"):
        async with app.run_test() as pilot:
            session = _make_session(state=AgentState.IDLE)
            app.post_message(SessionChanged(session))
            await pilot.pause()

            table = app.query_one("#sessions")
            row = table.get_row_at(0)
            name_cell = row[2]
            assert not name_cell.plain.startswith("\u25b8")
