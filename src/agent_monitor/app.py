"""Agent Monitor TUI application."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable, Footer, Header
from textual.worker import Worker, WorkerState

from agent_monitor.hyprland import HyprlandMonitor, get_event_socket_path
from agent_monitor.models import BRAILLE_SPINNER_CHARS, AgentSession, AgentState

SPINNER_FRAMES = list(BRAILLE_SPINNER_CHARS)  # [⠂, ⠐]
from agent_monitor.statusline import StatuslineWatcher
from agent_monitor.workspace import focus_window, switch_to_group

logger = logging.getLogger(__name__)


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


class AgentMonitorApp(App):
    TITLE = "Agent Monitor"
    CSS_PATH = "monitor.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "switch_group", "Switch"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._monitor: HyprlandMonitor | None = None
        self._watcher: StatuslineWatcher | None = None
        self._workspace_group_available: bool = True
        self._sessions: dict[str, AgentSession] = {}
        self._statusline_data: dict[str, dict] = {}
        self._statusline_columns_added: bool = False
        self._group_col_key = None
        self._spinner_frame: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="sessions")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        col_keys = table.add_columns("Group", "Session", "Status", "Task")
        self._group_col_key = col_keys[0]

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

    def _tick_spinners(self) -> None:
        """Animate spinner and update live duration for all ACTIVE sessions."""
        self._spinner_frame = (self._spinner_frame + 1) % len(SPINNER_FRAMES)
        char = SPINNER_FRAMES[self._spinner_frame]
        table = self.query_one(DataTable)
        status_col = 2  # Group, Session, Status

        for addr, session in self._sessions.items():
            if session.state == AgentState.ACTIVE and addr in table.rows:
                row_idx = table.get_row_index(addr)
                table.update_cell_at(
                    (row_idx, status_col),
                    Text(char, style="dark_orange"),
                )
                # Update live duration
                if self._statusline_columns_added and session.active_since is not None:
                    time_col = 5  # Group, Session, Status, Task, Context, Time
                    elapsed_ms = int((time.monotonic() - session.active_since) * 1000)
                    table.update_cell_at(
                        (row_idx, time_col),
                        Text(_render_duration(elapsed_ms)),
                    )

    async def _full_refresh(self) -> None:
        """Periodic full refresh via hyprctl clients."""
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
        table = self.query_one(DataTable)

        row_data = self._render_row(session)

        if session.address in table.rows:
            for col_idx, value in enumerate(row_data):
                table.update_cell_at((table.get_row_index(session.address), col_idx), value)
        else:
            table.add_row(*row_data, key=session.address)

        table.sort(self._group_col_key)
        self._update_subtitle()

    def on_session_removed(self, message: SessionRemoved) -> None:
        """Handle a session removal."""
        self._sessions.pop(message.address, None)
        table = self.query_one(DataTable)

        if message.address in table.rows:
            table.remove_row(message.address)

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

        # Ensure dynamic columns exist
        if not self._statusline_columns_added:
            table = self.query_one(DataTable)
            table.add_columns("Context", "Time")
            self._statusline_columns_added = True

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
        table = self.query_one(DataTable)
        if session.address not in table.rows:
            return
        row_data = self._render_row(session)
        for col_idx, value in enumerate(row_data):
            table.update_cell_at((table.get_row_index(session.address), col_idx), value)

    def _render_row(self, session: AgentSession) -> tuple:
        """Render a session into DataTable cell values."""
        group = session.workspace_group
        focus_prefix = "\u25b8 " if session.is_focused else ""

        if session.state == AgentState.ATTENTION:
            status = Text("\U0001f514", style="bold yellow")
            name_style = "bold yellow" if not session.is_focused else "bold underline yellow"
            name = Text(f"{focus_prefix}{session.session_name}", style=name_style)
            task_text = session.task_description
            task_style = "bold"
        elif session.state == AgentState.ACTIVE:
            status = Text(SPINNER_FRAMES[self._spinner_frame], style="dark_orange")
            name_style = "dim" if not session.is_focused else "bold"
            name = Text(f"{focus_prefix}{session.session_name}", style=name_style)
            task_text = session.task_description
            task_style = "dim"
        else:
            status = Text("\u2733", style="")
            name_style = "" if not session.is_focused else "bold"
            name = Text(f"{focus_prefix}{session.session_name}", style=name_style)
            task_text = session.task_description
            task_style = ""

        if len(task_text) > 60:
            task_text = task_text[:57] + "..."

        task_styled = Text(task_text, style=task_style)

        base = (group, name, status, task_styled)

        if self._statusline_columns_added:
            context = _render_context_bar(session.context_used_pct) if session.context_used_pct is not None else Text("")
            if session.active_since is not None:
                elapsed_ms = int((time.monotonic() - session.active_since) * 1000)
                duration = Text(_render_duration(elapsed_ms))
            else:
                duration = Text("")
            return base + (context, duration)

        return base

    def _update_subtitle(self) -> None:
        """Update the header subtitle with session counts."""
        sessions = self._sessions.values()
        active = sum(1 for s in sessions if s.state == AgentState.ACTIVE)
        attention = sum(1 for s in sessions if s.state == AgentState.ATTENTION)
        idle = sum(1 for s in sessions if s.state == AgentState.IDLE)

        parts = []
        if active:
            parts.append(f"{active} active")
        if attention:
            parts.append(f"{attention} attention")
        if idle:
            parts.append(f"{idle} idle")

        self.sub_title = ", ".join(parts) if parts else "No sessions"

    def action_refresh(self) -> None:
        """Manual refresh via 'r' key."""
        self.run_worker(self._full_refresh(), exclusive=False, name="manual-refresh")

    def action_switch_group(self) -> None:
        """Switch to the workspace group of the selected row."""
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return

        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        address = str(row_key.value)
        session = self._sessions.get(address)

        if session is None:
            return

        if not self._workspace_group_available:
            self.notify("workspace-group not found — workspace switching disabled", severity="warning")
            return

        self.run_worker(self._switch_and_focus(session), exclusive=False, name="switch-focus")

    async def _switch_and_focus(self, session: AgentSession) -> None:
        """Switch workspace group and focus the target window."""
        await switch_to_group(session.workspace_group)
        await asyncio.sleep(0.1)
        await focus_window(session.address)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Log worker failures for debugging."""
        if event.state == WorkerState.ERROR:
            logger.error("Worker %s failed: %s", event.worker.name, event.worker.error)


def main():
    app = AgentMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
