"""Agent Monitor TUI application."""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label
from textual.worker import Worker, WorkerState

from agent_monitor.codex_sidecar import run_codex_sidecar
from agent_monitor.hosts import HostAdapter, LocalHostAdapter, configured_host_adapter
from agent_monitor.hyprland import HyprlandMonitor, find_zellij_window, get_event_socket_path
from agent_monitor.models import (
    BRAILLE_SPINNER_CHARS,
    AgentRun,
    AgentSession,
    AgentState,
    AgentStatus,
    ClientTelemetry,
    HostInfo,
    HostSnapshot,
    Worktree,
)

SPINNER_FRAMES = list(BRAILLE_SPINNER_CHARS)  # [⠂, ⠐]
from agent_monitor.registry import read_devtools_worktrees
from agent_monitor.statusline import StatuslineWatcher
from agent_monitor.workspace import focus_window, move_window_to_workspace, switch_to_group
from agent_monitor.zellij import middle_workspace_for_group

logger = logging.getLogger(__name__)
IDLE_RECENT_MS = 10 * 60 * 1000


class SessionChanged(Message):
    """Posted when a Claude session is added or updated."""

    def __init__(self, session: AgentSession) -> None:
        super().__init__()
        self.session = session


class SessionRemoved(Message):
    """Posted when a Claude session is removed."""

    def __init__(self, address: str) -> None:
        super().__init__()
        self.address = address


class StatuslineDataChanged(Message):
    """Posted when statusline JSON data changes for a session."""

    def __init__(self, session_name: str, data: dict | None) -> None:
        super().__init__()
        self.session_name = session_name
        self.data = data


class WorkspaceGroupScreen(ModalScreen[int | None]):
    """Modal prompt for workspace group assignment."""

    DEFAULT_CSS = """
    WorkspaceGroupScreen {
        align: center middle;
    }

    WorkspaceGroupScreen > Vertical {
        width: 38;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: tall $primary;
    }

    WorkspaceGroupScreen Input {
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Workspace group (1-9)"),
            Input(placeholder="1-9", id="workspace-group-input"),
        )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value.isdigit() and 1 <= int(value) <= 9:
            self.dismiss(int(value))
            return
        self.notify("Workspace group must be 1-9", severity="warning")

    def action_cancel(self) -> None:
        self.dismiss(None)


def _render_duration(duration_ms: int) -> str:
    """Format duration in milliseconds as human-readable string."""
    total_seconds = duration_ms // 1000
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


def _render_context_bar(pct: float) -> Text:
    """Render a context usage bar with color coding."""
    pct = max(0.0, min(100.0, pct))
    filled = round(pct / 10)
    bar = "\u2588" * filled + "\u2591" * (10 - filled)
    label = f" {pct:.0f}%"

    if pct >= 90:
        style = "bold red"
    elif pct >= 70:
        style = "yellow"
    else:
        style = "green"

    return Text(bar + label, style=style)


def _truncate(value: str, limit: int = 60) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _render_run_time(run: AgentRun) -> Text:
    telemetry = run.telemetry
    if run.status == AgentStatus.ACTIVE and telemetry.active_since_ms is not None:
        now_ms = int(time.time() * 1000)
        return Text(_render_duration(max(0, now_ms - telemetry.active_since_ms)))
    if run.status in {AgentStatus.IDLE, AgentStatus.WAITING_INPUT, AgentStatus.WAITING_APPROVAL}:
        updated_at_ms = telemetry.updated_at_ms or telemetry.heartbeat_at_ms
        if updated_at_ms is not None:
            now_ms = int(time.time() * 1000)
            return Text(_render_duration(max(0, now_ms - updated_at_ms)))
    return Text("")


def _project_branch_from_cwd(cwd: str) -> tuple[str, str]:
    project_root = _git_output(cwd, ["rev-parse", "--show-toplevel"])
    project = os.path.basename(project_root) if project_root else os.path.basename(cwd)
    branch = _git_output(cwd, ["branch", "--show-current"])
    if not branch:
        branch = _git_output(cwd, ["rev-parse", "--short", "HEAD"])
    return project, branch


def _git_output(cwd: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            check=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def _repo_label(project: str, branch: str) -> str:
    if project and branch:
        return f"{project}/{branch}"
    return project or branch


def _render_port(port: int | None, *, is_open: bool | None = None) -> Text:
    if port is None:
        return Text("")
    if is_open is None:
        is_open = _is_port_open(port)
    style = "bold" if is_open else "dim"
    return Text(str(port), style=style)


def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.05):
            return True
    except OSError:
        return False


def _port_for_worktree(worktree: Worktree | None) -> int | None:
    if worktree is None:
        return None
    return worktree.port or worktree.tidewave_port


def _is_recent_idle(telemetry: ClientTelemetry) -> bool:
    updated_at_ms = telemetry.updated_at_ms or telemetry.heartbeat_at_ms
    if updated_at_ms is None:
        return False
    now_ms = int(time.time() * 1000)
    return 0 <= now_ms - updated_at_ms <= IDLE_RECENT_MS


def _run_sort_key(run: AgentRun) -> tuple:
    assigned_rank = 0 if run.workspace_group is not None else 1
    if run.status == AgentStatus.STOPPED:
        assigned_rank = 2 if run.workspace_group is None else assigned_rank
    status_rank = {
        AgentStatus.WAITING_INPUT: 0,
        AgentStatus.WAITING_APPROVAL: 0,
        AgentStatus.ACTIVE: 1,
        AgentStatus.IDLE: 2,
        AgentStatus.RUNNING: 3,
        AgentStatus.ERROR: 4,
        AgentStatus.STOPPED: 5,
    }.get(run.status, 6)
    group = run.workspace_group if run.workspace_group is not None else 99
    return (assigned_rank, group, status_rank, run.worktree_id, run.id)


def _worktree_sort_key(worktree: Worktree) -> tuple:
    return (2, 99, 5, worktree.project, worktree.branch)


def _session_sort_key(session: AgentSession) -> tuple:
    status_rank = {
        AgentState.ATTENTION: 0,
        AgentState.ACTIVE: 1,
        AgentState.IDLE: 2,
    }[session.state]
    return (0, session.workspace_group, status_rank, session.session_name, session.address)


class AgentMonitorApp(App):
    TITLE = "Agent Monitor"
    CSS_PATH = "monitor.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "assign_group", "Assign WS"),
        Binding("enter", "open_selected", "Open"),
    ]

    def __init__(self, host_adapter: HostAdapter | None = None) -> None:
        super().__init__()
        self._host_adapter = host_adapter or configured_host_adapter()
        self._snapshot: HostSnapshot | None = None
        self._worktrees: dict[str, Worktree] = {}
        self._snapshot_runs: dict[str, AgentRun] = {}
        self._worktree_rows: dict[str, Worktree] = {}
        self._monitor: HyprlandMonitor | None = None
        self._watcher: StatuslineWatcher | None = None
        self._workspace_group_available: bool = True
        self._sessions: dict[str, AgentSession] = {}
        self._statusline_data: dict[str, dict] = {}
        self._spinner_frame: int = 0
        self._cwd_project_branch_cache: dict[str, tuple[str, str]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="sessions")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("WS", "S", "Repo", "Port", "Ctx", "Time")

        # Prerequisite checks
        if not shutil.which("hyprctl"):
            self.notify(
                "hyprctl not found — agent-monitor requires Hyprland",
                severity="error",
                timeout=10,
            )
            self.exit(return_code=1)
            return

        try:
            get_event_socket_path()
        except FileNotFoundError:
            self.notify(
                "Cannot find Hyprland event socket",
                severity="error",
                timeout=10,
            )
            self.exit(return_code=1)
            return

        if not shutil.which("workspace-group"):
            self._workspace_group_available = False
            logger.warning("workspace-group not found on PATH; workspace switching disabled")

        self._refresh_snapshot_rows()
        self._start_monitor()
        self.set_interval(2.0, self._full_refresh)
        self.set_interval(0.48, self._tick_spinners)
        self._update_subtitle()

    def _start_monitor(self) -> None:
        """Start the Hyprland event monitor and statusline watcher as background workers."""
        self.run_worker(self._run_monitor(), exclusive=True, group="hyprland", name="hyprland-monitor")
        self.run_worker(self._run_statusline_watcher(), exclusive=True, group="statusline", name="statusline-watcher")

    async def _run_monitor(self) -> None:
        """Worker coroutine: create HyprlandMonitor and listen for events."""

        async def on_session_update(session: AgentSession) -> None:
            self.post_message(SessionChanged(session))

        async def on_session_remove(address: str) -> None:
            self.post_message(SessionRemoved(address))

        self._monitor = HyprlandMonitor(
            on_session_update=on_session_update,
            on_session_remove=on_session_remove,
        )
        await self._monitor.start()

    async def _run_statusline_watcher(self) -> None:
        """Worker coroutine: create StatuslineWatcher and watch for file changes."""

        def on_statusline_update(name: str, data: dict | None) -> None:
            self.post_message(StatuslineDataChanged(name, data))

        self._watcher = StatuslineWatcher(on_update=on_statusline_update)
        await self._watcher.watch()

    def _refresh_snapshot_rows(self) -> None:
        """Refresh registry-backed worktree/run rows."""
        snapshot = self._host_adapter.snapshot()
        self._snapshot = snapshot
        self._worktrees = {worktree.id: worktree for worktree in snapshot.worktrees}
        self._snapshot_runs = {self._run_row_key(run.id): run for run in snapshot.agent_runs}
        run_worktree_ids = {run.worktree_id for run in snapshot.agent_runs}
        self._worktree_rows = {
            self._worktree_row_key(worktree.id): worktree
            for worktree in snapshot.worktrees
            if worktree.id not in run_worktree_ids
        }
        self._rebuild_table()
        self._update_subtitle()

    @staticmethod
    def _run_row_key(run_id: str) -> str:
        return f"run:{run_id}"

    @staticmethod
    def _worktree_row_key(worktree_id: str) -> str:
        return f"worktree:{worktree_id}"

    @staticmethod
    def _session_row_key(address: str) -> str:
        return f"window:{address}"

    def _tick_spinners(self) -> None:
        """Animate spinner and update live duration for active rows."""
        self._spinner_frame = (self._spinner_frame + 1) % len(SPINNER_FRAMES)
        char = SPINNER_FRAMES[self._spinner_frame]
        table = self.query_one(DataTable)
        status_col = 1  # WS, S, Repo, Port

        for addr, session in self._sessions.items():
            row_key = self._session_row_key(addr)
            if session.state == AgentState.ACTIVE and row_key in table.rows:
                row_idx = table.get_row_index(row_key)
                table.update_cell_at(
                    (row_idx, status_col),
                    Text(char, style="dark_orange"),
                )
                # Update live duration
                if session.active_since is not None:
                    time_col = 5  # WS, S, Repo, Port, Ctx, Time
                    elapsed_ms = int((time.monotonic() - session.active_since) * 1000)
                    table.update_cell_at(
                        (row_idx, time_col),
                        Text(_render_duration(elapsed_ms)),
                    )

        for row_key, run in self._snapshot_runs.items():
            if run.status == AgentStatus.ACTIVE and row_key in table.rows:
                row_idx = table.get_row_index(row_key)
                table.update_cell_at(
                    (row_idx, status_col),
                    Text(char, style="dark_orange"),
                )
                table.update_cell_at(
                    (row_idx, 5),
                    _render_run_time(run),
                )

    async def _full_refresh(self) -> None:
        """Periodic full refresh via registries and hyprctl clients."""
        self._refresh_snapshot_rows()
        if self._monitor is not None:
            await self._monitor.refresh()

    def _find_statusline_match(self, session: AgentSession) -> dict | None:
        """Find matching statusline data by CWD (preferred) or session name."""
        if session.cwd:
            for key, data in self._statusline_data.items():
                sl_cwd = data.get("cwd")
                if sl_cwd and os.path.basename(sl_cwd) == session.cwd:
                    return data
        return self._statusline_data.get(session.session_name)

    def on_session_changed(self, message: SessionChanged) -> None:
        """Handle a session add/update."""
        session = message.session

        # Track active_since for live duration display
        old = self._sessions.get(session.address)
        if session.state == AgentState.ACTIVE:
            if old and old.state == AgentState.ACTIVE and old.active_since is not None:
                session.active_since = old.active_since
            else:
                session.active_since = time.monotonic()
        else:
            session.active_since = None

        # Merge pending statusline data if available
        sl_data = self._find_statusline_match(session)
        if sl_data:
            self._apply_statusline_to_session(session, sl_data)

        self._sessions[session.address] = session
        self._rebuild_table()
        self._update_subtitle()

    def on_session_removed(self, message: SessionRemoved) -> None:
        """Handle a session removal."""
        self._sessions.pop(message.address, None)
        row_key = self._session_row_key(message.address)
        _ = row_key
        self._rebuild_table()

        self._update_subtitle()

    def _find_session_for_statusline(self, name: str, data: dict | None) -> AgentSession | None:
        """Find a session matching statusline data by CWD (preferred) or name."""
        if data:
            sl_cwd = data.get("cwd")
            if sl_cwd:
                cwd_base = os.path.basename(sl_cwd)
                for session in self._sessions.values():
                    if session.cwd and session.cwd == cwd_base:
                        return session
        # Fallback: match by statusline filename stem == session_name
        for session in self._sessions.values():
            if session.session_name == name:
                return session
        return None

    def on_statusline_data_changed(self, message: StatuslineDataChanged) -> None:
        """Handle statusline file data update."""
        name = message.session_name

        if message.data is None:
            self._statusline_data.pop(name, None)
            # Clear statusline fields from matching session
            matched = self._find_session_for_statusline(name, None)
            if matched:
                self._clear_statusline_from_session(matched)
                self._update_row(matched)
            return

        self._statusline_data[name] = message.data

        # Find matching session and update it
        matched = self._find_session_for_statusline(name, message.data)
        if matched:
            self._apply_statusline_to_session(matched, message.data)
            self._update_row(matched)

    def _apply_statusline_to_session(self, session: AgentSession, data: dict) -> None:
        """Merge statusline data fields into an AgentSession."""
        session.cost_usd = data.get("cost_usd")
        session.duration_ms = data.get("duration_ms")
        session.context_used_pct = data.get("context_used_pct")
        session.model_name = data.get("model_name")
        session.lines_added = data.get("lines_added")
        session.lines_removed = data.get("lines_removed")

    def _clear_statusline_from_session(self, session: AgentSession) -> None:
        """Clear all optional statusline fields from a session."""
        session.cost_usd = None
        session.duration_ms = None
        session.context_used_pct = None
        session.model_name = None
        session.lines_added = None
        session.lines_removed = None

    def _update_row(self, session: AgentSession) -> None:
        """Re-render a single session row in the DataTable."""
        _ = session
        self._rebuild_table()

    def _render_run_row(self, snapshot: HostSnapshot, run: AgentRun) -> tuple:
        """Render a registry-backed agent run into DataTable cell values."""
        worktree = self._worktrees.get(run.worktree_id)
        project, branch = self._project_branch_for_run(run, worktree)
        row_style = ""
        if run.status == AgentStatus.STOPPED:
            row_style = "dim"
        elif run.status == AgentStatus.IDLE and _is_recent_idle(run.telemetry):
            row_style = "dark_orange"
        ws = Text(str(run.workspace_group) if run.workspace_group is not None else "", style=row_style)
        repo = Text(_truncate(_repo_label(project, branch), 48), style=row_style)
        context = (
            _render_context_bar(run.telemetry.context_used_pct)
            if run.telemetry.context_used_pct is not None
            else Text("")
        )
        return (
            ws,
            self._render_status(run.status, telemetry=run.telemetry),
            repo,
            _render_port(_port_for_worktree(worktree)),
            context,
            _render_run_time(run),
        )

    def _project_branch_for_run(self, run: AgentRun, worktree: Worktree | None) -> tuple[str, str]:
        if worktree is not None:
            return worktree.project, worktree.branch
        if not run.cwd:
            return "", ""
        cwd = os.path.realpath(os.path.expanduser(run.cwd))
        cached = self._cwd_project_branch_cache.get(cwd)
        if cached is not None:
            return cached
        value = _project_branch_from_cwd(cwd)
        self._cwd_project_branch_cache[cwd] = value
        return value

    def _render_worktree_row(self, snapshot: HostSnapshot, worktree: Worktree) -> tuple:
        """Render a worktree without a concrete agent run."""
        _ = snapshot
        return (
            "",
            self._render_status(AgentStatus.STOPPED),
            Text(_truncate(_repo_label(worktree.project, worktree.branch), 48), style="dim"),
            _render_port(_port_for_worktree(worktree)),
            Text(""),
            Text(""),
        )

    def _render_row(self, session: AgentSession) -> tuple:
        """Render a session into DataTable cell values."""
        group = session.workspace_group
        focus_prefix = "\u25b8 " if session.is_focused else ""

        if session.state == AgentState.ATTENTION:
            name_style = "bold yellow" if not session.is_focused else "bold underline yellow"
            name = Text(f"{focus_prefix}{session.session_name}", style=name_style)
        elif session.state == AgentState.ACTIVE:
            name_style = "dim" if not session.is_focused else "bold"
            name = Text(f"{focus_prefix}{session.session_name}", style=name_style)
        else:
            name_style = "" if not session.is_focused else "bold"
            name = Text(f"{focus_prefix}{session.session_name}", style=name_style)

        context = _render_context_bar(session.context_used_pct) if session.context_used_pct is not None else Text("")
        if session.active_since is not None:
            elapsed_ms = int((time.monotonic() - session.active_since) * 1000)
            duration = Text(_render_duration(elapsed_ms))
        else:
            duration = Text("")
        return (
            str(group),
            self._render_session_status(session),
            name,
            Text(""),
            context,
            duration,
        )

    def _render_status(self, status: AgentStatus, *, telemetry: ClientTelemetry | None = None) -> Text:
        if status == AgentStatus.STOPPED:
            return Text("S", style="dim")
        if status == AgentStatus.ACTIVE:
            return Text(SPINNER_FRAMES[self._spinner_frame], style="dark_orange")
        if status in {AgentStatus.WAITING_INPUT, AgentStatus.WAITING_APPROVAL}:
            return Text("W", style="bold yellow")
        if status == AgentStatus.ERROR:
            return Text("E", style="bold red")
        if status == AgentStatus.IDLE:
            if telemetry is not None and _is_recent_idle(telemetry):
                return Text("I", style="dark_orange")
            return Text("I")
        if status == AgentStatus.RUNNING:
            return Text("R")
        return Text("?", style="dim")

    def _render_session_status(self, session: AgentSession) -> Text:
        if session.state == AgentState.ATTENTION:
            return Text("W", style="bold yellow")
        if session.state == AgentState.ACTIVE:
            return Text(SPINNER_FRAMES[self._spinner_frame], style="dark_orange")
        return Text("I")

    def _rebuild_table(self) -> None:
        table = self.query_one(DataTable)
        selected_row_key = None
        selected_row_index = table.cursor_row
        if table.row_count and table.is_valid_coordinate(table.cursor_coordinate):
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            selected_row_key = str(row_key.value)

        rows: list[tuple[str, tuple, tuple]] = []
        snapshot = self._snapshot or HostSnapshot(host=HostInfo(name="local"))

        for address, session in self._sessions.items():
            row_key = self._session_row_key(address)
            rows.append((row_key, self._render_row(session), _session_sort_key(session)))

        for row_key, run in self._snapshot_runs.items():
            rows.append((row_key, self._render_run_row(snapshot, run), _run_sort_key(run)))

        for row_key, worktree in self._worktree_rows.items():
            rows.append((row_key, self._render_worktree_row(snapshot, worktree), _worktree_sort_key(worktree)))

        rows.sort(key=lambda item: item[2])
        table.clear(columns=False)
        for row_key, row_data, _sort_key in rows:
            table.add_row(*row_data, key=row_key)
        if table.row_count == 0:
            return
        if selected_row_key in table.rows:
            selected_row_index = table.get_row_index(selected_row_key)
        else:
            selected_row_index = min(selected_row_index, table.row_count - 1)
        table.move_cursor(row=selected_row_index, column=0, scroll=False)

    def _update_subtitle(self) -> None:
        """Update the header subtitle with session counts."""
        sessions = self._sessions.values()
        snapshot_runs = self._snapshot_runs.values()
        active = sum(1 for s in sessions if s.state == AgentState.ACTIVE)
        attention = sum(1 for s in sessions if s.state == AgentState.ATTENTION)
        idle = sum(1 for s in sessions if s.state == AgentState.IDLE)
        stopped = (
            sum(1 for run in snapshot_runs if run.status == AgentStatus.STOPPED)
            + len(self._worktree_rows)
        )
        running = sum(1 for run in snapshot_runs if run.status == AgentStatus.RUNNING)

        parts = []
        if self._snapshot is not None:
            parts.append(self._snapshot.host.name)
        if active:
            parts.append(f"{active} active")
        if attention:
            parts.append(f"{attention} attention")
        if idle:
            parts.append(f"{idle} idle")
        if running:
            parts.append(f"{running} running")
        if stopped:
            parts.append(f"{stopped} stopped")

        self.sub_title = " · ".join(parts) if parts else "No sessions"

    def action_refresh(self) -> None:
        """Manual refresh via 'r' key."""
        self.run_worker(self._full_refresh(), exclusive=False, name="manual-refresh")

    def action_assign_group(self) -> None:
        """Assign a workspace group to the selected registry-backed run."""
        run = self._selected_snapshot_run_or_default()
        if run is None:
            self.notify("Select a registered run to assign a workspace group", severity="warning")
            return
        self.push_screen(WorkspaceGroupScreen(), callback=lambda group: self._handle_group_assignment(run, group))

    def _handle_group_assignment(self, run: AgentRun, workspace_group: int | None) -> None:
        if workspace_group is None:
            return
        self._assign_run_workspace_group(run, workspace_group)

    def _assign_run_workspace_group(self, run: AgentRun, workspace_group: int) -> None:
        try:
            self._host_adapter.set_workspace_group(run, workspace_group)
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return
        self._refresh_snapshot_rows()
        self.notify(f"Assigned workspace group {workspace_group}")

    def _selected_snapshot_run(self) -> AgentRun | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return self._snapshot_runs.get(str(row_key.value))

    def _selected_snapshot_run_or_default(self) -> AgentRun | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return self._run_for_row_key(str(row_key.value))

    def action_open_selected(self) -> None:
        """Open the selected live window or registry-backed run."""
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return

        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        self._open_row_key(str(row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open rows selected by DataTable's Enter handling."""
        event.stop()
        self._open_row_key(str(event.row_key.value))

    def _open_row_key(self, selected_key: str) -> None:
        session = None
        if selected_key.startswith("window:"):
            session = self._sessions.get(selected_key.removeprefix("window:"))

        run = self._run_for_row_key(selected_key)
        if session is None and run is None:
            return

        if session is not None and not self._workspace_group_available:
            self.notify("workspace-group not found — workspace switching disabled", severity="warning")
            return

        if session is not None:
            self.run_worker(self._switch_and_focus(session), exclusive=False, name="switch-focus")
        elif run is not None:
            self.run_worker(self._open_run(run), exclusive=False, name="open-run")

    def _run_for_row_key(self, row_key: str) -> AgentRun | None:
        run = self._snapshot_runs.get(row_key)
        if run is not None:
            return run
        worktree = self._worktree_rows.get(row_key)
        if worktree is None:
            return None
        return AgentRun.default_codex_for_worktree(worktree)

    async def _switch_and_focus(self, session: AgentSession) -> None:
        """Switch workspace group and focus the target window."""
        await switch_to_group(session.workspace_group)
        await asyncio.sleep(0.1)
        await focus_window(session.address)

    async def _open_run(self, run: AgentRun) -> None:
        """Focus an existing zellij window or attach in a new terminal."""
        existing_window = None
        if run.zellij_session:
            existing_window = await find_zellij_window(run.zellij_session)
        target_group = run.workspace_group
        if target_group is None and existing_window:
            workspace_id = existing_window.get("workspace_id")
            if isinstance(workspace_id, int) and workspace_id > 0 and workspace_id % 10 != 0:
                target_group = workspace_id % 10

        if target_group is not None:
            if self._workspace_group_available:
                await switch_to_group(target_group)
                await asyncio.sleep(0.1)
            else:
                self.notify("workspace-group not found — opening session without workspace switch", severity="warning")

        if existing_window:
            if target_group is not None:
                move_window_to_workspace(existing_window["address"], middle_workspace_for_group(target_group))
            await focus_window(existing_window["address"])
            return

        if not self._host_adapter.open_run(run):
            self.notify("No supported terminal found for zellij attach", severity="warning")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Log worker failures for debugging."""
        if event.state == WorkerState.ERROR:
            logger.error("Worker %s failed: %s", event.worker.name, event.worker.error)


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "host-snapshot":
        parser = argparse.ArgumentParser(prog="agent-monitor host-snapshot")
        parser.add_argument("--json", action="store_true", help="print the snapshot as JSON")
        parser.add_argument("--devtools-registry", help="path to dev-tools instances.json")
        parser.add_argument("--overlay", help="path to agent-monitor sessions.json")
        parser.add_argument("--sidecar-runs-dir", help="path to agent-monitor sidecar runs directory")
        args = parser.parse_args(argv[1:])
        snapshot = LocalHostAdapter(
            devtools_registry_path=args.devtools_registry,
            overlay_path=args.overlay,
            sidecar_runs_dir=args.sidecar_runs_dir,
        ).snapshot()
        if args.json:
            print(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
            return
        print(snapshot)
        return

    if argv and argv[0] == "open-run":
        parser = argparse.ArgumentParser(prog="agent-monitor open-run")
        parser.add_argument("target", help="agent run id or dev-tools worktree id")
        parser.add_argument("--json", action="store_true", help="print a JSON response")
        parser.add_argument("--devtools-registry", help="path to dev-tools instances.json")
        parser.add_argument("--overlay", help="path to agent-monitor sessions.json")
        parser.add_argument("--sidecar-runs-dir", help="path to agent-monitor sidecar runs directory")
        args = parser.parse_args(argv[1:])
        adapter = LocalHostAdapter(
            devtools_registry_path=args.devtools_registry,
            overlay_path=args.overlay,
            sidecar_runs_dir=args.sidecar_runs_dir,
        )
        _handle_open_run_command(adapter, args.target, json_output=args.json)
        return

    if argv and argv[0] == "set-group":
        parser = argparse.ArgumentParser(prog="agent-monitor set-group")
        parser.add_argument("target", help="agent run id or dev-tools worktree id")
        parser.add_argument("group", type=int, help="workspace group 1-9")
        parser.add_argument("--json", action="store_true", help="print a JSON response")
        parser.add_argument("--devtools-registry", help="path to dev-tools instances.json")
        parser.add_argument("--overlay", help="path to agent-monitor sessions.json")
        parser.add_argument("--sidecar-runs-dir", help="path to agent-monitor sidecar runs directory")
        args = parser.parse_args(argv[1:])
        adapter = LocalHostAdapter(
            devtools_registry_path=args.devtools_registry,
            overlay_path=args.overlay,
            sidecar_runs_dir=args.sidecar_runs_dir,
        )
        _handle_set_group_command(adapter, args.target, args.group, json_output=args.json)
        return

    if argv and argv[0] == "codex":
        parser = argparse.ArgumentParser(prog="agent-monitor codex")
        parser.add_argument("--run-id", help="agent-monitor run id; defaults to <worktree-id>::<run-name>")
        parser.add_argument("--run-name", default="main", help="run name suffix when --run-id is not provided")
        parser.add_argument("--worktree-id", help="dev-tools worktree id; inferred from cwd when omitted")
        parser.add_argument("--cwd", help="worktree cwd; defaults to current directory")
        parser.add_argument("--zellij-session", help="zellij session name; defaults to $ZELLIJ_SESSION_NAME")
        parser.add_argument("--codex-thread-id")
        parser.add_argument("--keep-status", action="store_true", help="keep stopped sidecar status on clean exit")
        parser.add_argument("--devtools-registry", help="path to dev-tools instances.json")
        parser.add_argument("--sidecar-runs-dir", help="path to agent-monitor sidecar runs directory")
        parser.add_argument("--status-path")
        parser.add_argument("--heartbeat-interval", type=float, default=5.0)
        parser.add_argument("codex_args", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv[1:])
        return_code = _handle_codex_command(args, parser)
        raise SystemExit(return_code)

    if argv and argv[0] == "codex-sidecar":
        parser = argparse.ArgumentParser(prog="agent-monitor codex-sidecar")
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--worktree-id")
        parser.add_argument("--cwd")
        parser.add_argument("--zellij-session")
        parser.add_argument("--codex-thread-id")
        parser.add_argument("--sidecar-runs-dir")
        parser.add_argument("--status-path")
        parser.add_argument("--heartbeat-interval", type=float, default=5.0)
        parser.add_argument("command", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv[1:])
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("command is required after --")

        return_code = run_codex_sidecar(
            run_id=args.run_id,
            worktree_id=args.worktree_id,
            cwd=args.cwd,
            zellij_session=args.zellij_session,
            codex_thread_id=args.codex_thread_id,
            runs_dir=args.sidecar_runs_dir,
            status_path=args.status_path,
            heartbeat_interval=args.heartbeat_interval,
            command=command,
        )
        raise SystemExit(return_code)

    app = AgentMonitorApp()
    app.run()


def _handle_open_run_command(
    adapter: LocalHostAdapter,
    target: str,
    *,
    json_output: bool,
) -> None:
    snapshot = adapter.snapshot()
    run, resolved_as = _resolve_run_or_worktree(snapshot, target)
    if run is None:
        _finish_cli_response(
            _error_payload("not_found", f"run or worktree not found: {target}", command="open-run", target=target),
            json_output=json_output,
            exit_code=1,
        )
        return

    opened = adapter.open_run(run)
    if not opened:
        _finish_cli_response(
            _error_payload("open_failed", f"failed to open run: {run.id}", command="open-run", target=target),
            json_output=json_output,
            exit_code=1,
        )
        return

    refreshed_run = _resolve_exact_run(adapter.snapshot(), run.id) or run
    _finish_cli_response(
        {
            "ok": True,
            "command": "open-run",
            "target": target,
            "action": adapter.last_open_action,
            "resolved_as": resolved_as,
            "run": refreshed_run.to_dict(),
        },
        json_output=json_output,
    )


def _handle_set_group_command(
    adapter: LocalHostAdapter,
    target: str,
    workspace_group: int,
    *,
    json_output: bool,
) -> None:
    snapshot = adapter.snapshot()
    run, resolved_as = _resolve_run_or_worktree(snapshot, target)
    if run is None:
        _finish_cli_response(
            _error_payload("not_found", f"run or worktree not found: {target}", command="set-group", target=target),
            json_output=json_output,
            exit_code=1,
        )
        return

    try:
        updated_run = adapter.set_workspace_group(run, workspace_group)
    except ValueError as exc:
        _finish_cli_response(
            _error_payload("invalid_group", str(exc), command="set-group", target=target),
            json_output=json_output,
            exit_code=1,
        )
        return

    _finish_cli_response(
        {
            "ok": True,
            "command": "set-group",
            "target": target,
            "resolved_as": resolved_as,
            "run": updated_run.to_dict(),
        },
        json_output=json_output,
    )


def _handle_codex_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    cwd = os.path.realpath(os.path.expanduser(args.cwd or os.getcwd()))
    worktree_id = args.worktree_id
    matched_devtools_worktree = False
    if not worktree_id:
        worktree = _find_worktree_for_cwd(cwd, read_devtools_worktrees(args.devtools_registry))
        if worktree is not None:
            worktree_id = worktree.id
            matched_devtools_worktree = True
    else:
        matched_devtools_worktree = any(
            worktree.id == worktree_id
            for worktree in read_devtools_worktrees(args.devtools_registry)
        )

    run_id = args.run_id
    if not run_id:
        if not worktree_id:
            parser.error("could not infer worktree id from cwd; pass --worktree-id or --run-id")
        run_id = f"{worktree_id}::{args.run_name}"

    if not worktree_id:
        worktree_id = _worktree_id_from_run_id(run_id)

    command = list(args.codex_args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        command = ["codex", "--cd", cwd]
    elif command[0] != "codex":
        command = ["codex", *command]

    return run_codex_sidecar(
        run_id=run_id,
        worktree_id=worktree_id,
        cwd=cwd,
        zellij_session=args.zellij_session or os.environ.get("ZELLIJ_SESSION_NAME"),
        codex_thread_id=args.codex_thread_id,
        runs_dir=args.sidecar_runs_dir,
        status_path=args.status_path,
        heartbeat_interval=args.heartbeat_interval,
        cleanup_stopped_status=not args.keep_status and not matched_devtools_worktree,
        command=command,
    )


def _resolve_run_or_worktree(snapshot: HostSnapshot, target: str) -> tuple[AgentRun | None, str | None]:
    run = _resolve_exact_run(snapshot, target)
    if run is not None:
        return run, "run"

    worktree = _resolve_exact_worktree(snapshot, target)
    if worktree is not None:
        default_run = _resolve_exact_run(snapshot, f"{worktree.id}::main")
        if default_run is not None:
            return default_run, "worktree"
        return AgentRun.default_codex_for_worktree(worktree), "worktree"

    if target.endswith("::main"):
        worktree = _resolve_exact_worktree(snapshot, target.removesuffix("::main"))
        if worktree is not None:
            return AgentRun.default_codex_for_worktree(worktree), "default-run"

    return None, None


def _resolve_exact_run(snapshot: HostSnapshot, run_id: str) -> AgentRun | None:
    for run in snapshot.agent_runs:
        if run.id == run_id:
            return run
    return None


def _resolve_exact_worktree(snapshot: HostSnapshot, worktree_id: str) -> Worktree | None:
    for worktree in snapshot.worktrees:
        if worktree.id == worktree_id:
            return worktree
    return None


def _find_worktree_for_cwd(cwd: str, worktrees: list[Worktree]) -> Worktree | None:
    cwd_path = os.path.realpath(os.path.expanduser(cwd))
    matches = [
        worktree
        for worktree in worktrees
        if worktree.path and _path_is_inside(cwd_path, os.path.realpath(os.path.expanduser(worktree.path)))
    ]
    if not matches:
        return None
    return max(matches, key=lambda worktree: len(os.path.realpath(os.path.expanduser(worktree.path))))


def _path_is_inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _worktree_id_from_run_id(run_id: str) -> str:
    if "::" not in run_id:
        return run_id
    return run_id.rsplit("::", 1)[0]


def _finish_cli_response(payload: dict, *, json_output: bool, exit_code: int = 0) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload.get("ok"):
        run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        print(f"{payload['command']} ok: {run.get('id', payload.get('target', ''))}")
    else:
        print(payload.get("error", {}).get("message", "agent-monitor command failed"), file=sys.stderr)
    if exit_code:
        raise SystemExit(exit_code)


def _error_payload(code: str, message: str, *, command: str, target: str) -> dict:
    return {
        "ok": False,
        "command": command,
        "target": target,
        "error": {
            "code": code,
            "message": message,
        },
    }


if __name__ == "__main__":
    main()
