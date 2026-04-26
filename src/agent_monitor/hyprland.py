"""Hyprland integration — event socket listener and client state."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Awaitable, Callable

from agent_monitor.models import AgentSession, AgentState, TERMINAL_CLASSES, parse_window_title
from agent_monitor.procfs import (
    _build_zellij_socket_map,
    find_claude_processes,
    find_zellij_session_for_terminal,
)

logger = logging.getLogger(__name__)


def normalize_address(address: str) -> str:
    """Strip the 0x prefix from a Hyprland window address."""
    if address.startswith("0x"):
        return address[2:]
    return address


def parse_event_line(line: str) -> dict | None:
    """Parse a single Hyprland event socket line.

    Event format: EVENT_NAME>>DATA
    Returns a dict with event-specific fields, or None for unknown/malformed events.
    """
    if ">>" not in line:
        return None

    event_name, _, data = line.partition(">>")

    if event_name == "windowtitlev2":
        if "," not in data:
            return None
        address, _, title = data.partition(",")
        return {"event": "windowtitlev2", "address": address, "title": title}

    elif event_name == "openwindow":
        parts = data.split(",", 3)
        if len(parts) != 4:
            return None
        address, ws_id_str, window_class, title = parts
        try:
            ws_id = int(ws_id_str)
        except ValueError:
            return None
        return {
            "event": "openwindow",
            "address": address,
            "workspace_id": ws_id,
            "window_class": window_class,
            "title": title,
        }

    elif event_name == "closewindow":
        return {"event": "closewindow", "address": data}

    elif event_name == "activewindowv2":
        return {"event": "activewindowv2", "address": data}

    elif event_name == "movewindowv2":
        parts = data.split(",", 2)
        if len(parts) != 3:
            return None
        address, ws_id_str, ws_name = parts
        try:
            ws_id = int(ws_id_str)
        except ValueError:
            return None
        return {
            "event": "movewindowv2",
            "address": address,
            "workspace_id": ws_id,
            "workspace_name": ws_name,
        }

    return None


def get_event_socket_path() -> str:
    """Discover the Hyprland event socket path.

    Tries $HYPRLAND_INSTANCE_SIGNATURE first, then scans /run/user/{uid}/hypr/.
    Raises FileNotFoundError if no socket is found.
    """
    uid = os.getuid()
    hypr_dir = f"/run/user/{uid}/hypr"

    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig:
        path = f"{hypr_dir}/{sig}/.socket2.sock"
        if os.path.exists(path):
            return path

    # Fallback: scan for subdirs with .socket2.sock
    if os.path.exists(hypr_dir):
        try:
            for entry in os.listdir(hypr_dir):
                candidate = f"{hypr_dir}/{entry}/.socket2.sock"
                if os.path.isdir(f"{hypr_dir}/{entry}") and os.path.exists(candidate):
                    return candidate
        except OSError:
            pass

    raise FileNotFoundError(
        "Hyprland event socket not found. "
        "Is Hyprland running? Check $HYPRLAND_INSTANCE_SIGNATURE."
    )


async def fetch_clients() -> list[dict]:
    """Fetch window clients from hyprctl clients -j.

    Returns parsed JSON list, or empty list on failure/timeout.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "hyprctl", "clients", "-j",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("hyprctl clients timed out")
        if proc is not None:
            proc.kill()
            await proc.communicate()
        return []
    except FileNotFoundError:
        logger.warning("hyprctl not found on PATH")
        return []

    if proc.returncode != 0:
        logger.warning("hyprctl clients exited with code %d: %s", proc.returncode, stderr.decode())
        return []

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse hyprctl clients JSON output")
        return []


def fetch_clients_sync() -> list[dict]:
    """Fetch window clients from hyprctl clients -j for synchronous callers."""
    try:
        result = subprocess.run(
            ["hyprctl", "clients", "-j"],
            capture_output=True,
            check=True,
            timeout=5.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def fetch_active_window() -> str | None:
    """Fetch the currently focused window address via hyprctl activewindow -j.

    Returns normalized address, or None on failure/timeout.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "hyprctl", "activewindow", "-j",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("hyprctl activewindow timed out")
        if proc is not None:
            proc.kill()
            await proc.communicate()
        return None
    except FileNotFoundError:
        logger.warning("hyprctl not found on PATH")
        return None

    if proc.returncode != 0:
        logger.warning("hyprctl activewindow exited with code %d", proc.returncode)
        return None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse hyprctl activewindow JSON output")
        return None

    address = data.get("address", "")
    if not address:
        return None
    return normalize_address(address)


async def find_zellij_window(session_name: str) -> dict | None:
    """Find a Hyprland terminal window attached to a zellij session."""
    clients = await fetch_clients()
    return find_zellij_window_in_clients(session_name, clients)


def find_zellij_window_sync(session_name: str) -> dict | None:
    """Find a Hyprland terminal window attached to a zellij session."""
    clients = fetch_clients_sync()
    return find_zellij_window_in_clients(session_name, clients)


def find_zellij_window_in_clients(session_name: str, clients: list[dict]) -> dict | None:
    """Find a zellij session window from already-fetched Hyprland client data."""
    socket_map = _build_zellij_socket_map()
    for client in clients:
        window_class = client.get("class", "")
        if window_class not in TERMINAL_CLASSES:
            continue

        pid = client.get("pid")
        if not isinstance(pid, int):
            continue

        zellij_session = find_zellij_session_for_terminal(pid, socket_map=socket_map)
        if zellij_session != session_name:
            continue

        address = normalize_address(client.get("address", ""))
        if not address:
            continue

        return {
            "address": address,
            "workspace_id": client.get("workspace", {}).get("id"),
            "window_class": window_class,
            "pid": pid,
        }
    return None


async def listen_events(
    socket_path: str,
    callback: Callable[[dict], Awaitable[None]],
) -> None:
    """Listen to the Hyprland event socket and dispatch parsed events.

    Reconnects with exponential backoff on disconnection.
    This coroutine runs indefinitely until cancelled.
    """
    backoff = 1.0
    max_backoff = 10.0

    while True:
        try:
            reader, _ = await asyncio.open_unix_connection(socket_path)
            logger.info("Connected to Hyprland event socket")
            backoff = 1.0  # Reset on successful connect

            buffer = b""
            while True:
                data = await reader.read(4096)
                if not data:
                    logger.warning("Event socket disconnected")
                    break

                buffer += data
                while b"\n" in buffer:
                    line_bytes, _, buffer = buffer.partition(b"\n")
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    event = parse_event_line(line)
                    if event is not None:
                        await callback(event)

        except (ConnectionError, OSError) as exc:
            logger.warning("Event socket error: %s", exc)
        except asyncio.CancelledError:
            raise

        logger.info("Reconnecting in %.1fs...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def _is_valid_workspace(workspace_id: int) -> bool:
    """Check if a workspace ID is valid for session tracking."""
    return workspace_id > 0 and workspace_id % 10 != 0


class HyprlandMonitor:
    """Manages Hyprland window tracking and Claude session state.

    Attributes:
        sessions: Claude sessions keyed by normalized address.
        on_session_update: Async callback when a session is added/changed.
        on_session_remove: Async callback when a session is removed (receives address).
    """

    def __init__(
        self,
        on_session_update: Callable[[AgentSession], Awaitable[None]] | None = None,
        on_session_remove: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.sessions: dict[str, AgentSession] = {}
        self._window_meta: dict[str, dict] = {}
        self._focused_address: str | None = None
        self.on_session_update = on_session_update
        self.on_session_remove = on_session_remove
        self._log = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def start(self) -> None:
        """Fetch initial state, then listen for events."""
        clients = await fetch_clients()
        self._populate_from_clients(clients)

        # Set initial focus
        active_addr = await fetch_active_window()
        if active_addr:
            self._focused_address = active_addr
            if active_addr in self.sessions:
                self.sessions[active_addr].is_focused = True

        self._log.info("Initial state: %d sessions from %d windows", len(self.sessions), len(self._window_meta))

        # Notify about all initial sessions
        if self.on_session_update:
            for session in self.sessions.values():
                await self.on_session_update(session)

        socket_path = get_event_socket_path()
        await listen_events(socket_path, self._dispatch_event)

    async def refresh(self) -> None:
        """Re-fetch all clients via hyprctl for periodic full sync."""
        clients = await fetch_clients()
        old_addresses = set(self.sessions.keys())
        self._window_meta.clear()
        self.sessions.clear()
        self._populate_from_clients(clients)

        new_addresses = set(self.sessions.keys())

        # Reapply focus based on tracked address
        if self._focused_address and self._focused_address in self.sessions:
            self.sessions[self._focused_address].is_focused = True

        # Notify about removed sessions (window truly gone)
        for addr in old_addresses - new_addresses:
            if self.on_session_remove:
                await self.on_session_remove(addr)

        # Notify about all current sessions (added or updated)
        for addr in new_addresses:
            if self.on_session_update:
                await self.on_session_update(self.sessions[addr])

    def _populate_from_clients(self, clients: list[dict]) -> None:
        """Build _window_meta and sessions from hyprctl clients output."""
        for client in clients:
            addr = normalize_address(client.get("address", ""))
            if not addr:
                continue

            window_class = client.get("class", "")
            workspace_id = client.get("workspace", {}).get("id", 0)
            pid = client.get("pid")
            title = client.get("title", "")

            self._window_meta[addr] = {
                "class": window_class,
                "workspace_id": workspace_id,
                "pid": pid,
            }

            if not _is_valid_workspace(workspace_id):
                continue

            parsed = parse_window_title(title, window_class)
            if parsed is None:
                continue

            self.sessions[addr] = AgentSession(
                address=addr,
                session_name=parsed["session_name"],
                task_description=parsed["task_description"],
                state=parsed["state"],
                workspace_id=workspace_id,
                window_class=window_class,
                status_char=parsed["status_char"],
                pid=pid,
            )

        self._resolve_session_cwds()

    def _resolve_session_cwds(self) -> None:
        """Resolve CWDs for sessions by correlating terminal PIDs with Claude processes.

        1. Build zellij socket map once (via ss + /proc/net/unix).
        2. For each session's terminal PID, find its Zellij session name.
        3. Find all Claude processes and their CWDs + Zellij session names.
        4. Match sessions to Claude CWDs via shared Zellij session name.
        """
        # Step 0: Build socket map once for all lookups
        socket_map = _build_zellij_socket_map()

        # Step 1: Map session address → zellij session name
        addr_to_zellij: dict[str, str] = {}
        for addr, session in self.sessions.items():
            pid = session.pid
            if pid is None:
                continue
            zellij_name = find_zellij_session_for_terminal(pid, socket_map=socket_map)
            if zellij_name:
                addr_to_zellij[addr] = zellij_name

        if not addr_to_zellij:
            return

        # Step 2: Find all Claude processes → {zellij_session_name: [cwd, ...]}
        claude_procs = find_claude_processes()
        zellij_to_cwds: dict[str, list[str]] = {}
        for proc in claude_procs:
            zname = proc.get("zellij_session_name")
            cwd = proc.get("cwd")
            if zname and cwd:
                zellij_to_cwds.setdefault(zname, []).append(cwd)

        # Step 3: Assign CWDs to sessions
        for addr, zellij_name in addr_to_zellij.items():
            cwds = zellij_to_cwds.get(zellij_name, [])
            if len(cwds) == 1:
                self.sessions[addr].cwd = os.path.basename(cwds[0])
            elif len(cwds) > 1:
                # Multiple Claude instances in same zellij session —
                # assign first unmatched CWD
                assigned = {s.cwd for s in self.sessions.values() if s.cwd}
                for cwd in cwds:
                    base = os.path.basename(cwd)
                    if base not in assigned:
                        self.sessions[addr].cwd = base
                        break

    async def _dispatch_event(self, event: dict) -> None:
        """Route a parsed event to the appropriate handler."""
        name = event["event"]

        if name == "activewindowv2":
            await self._handle_focus_change(event["address"])
            return

        addr = normalize_address(event.get("address", ""))
        had_session = addr in self.sessions

        if name == "windowtitlev2":
            self._handle_title_change(event["address"], event["title"])
        elif name == "openwindow":
            self._handle_window_open(event)
        elif name == "closewindow":
            self._handle_window_close(event["address"])
        elif name == "movewindowv2":
            self._handle_window_move(event["address"], event["workspace_id"])

        has_session = addr in self.sessions

        # Fire callbacks based on actual state transitions
        if had_session and not has_session:
            if self.on_session_remove and addr:
                await self.on_session_remove(addr)
        elif has_session:
            if self.on_session_update:
                await self.on_session_update(self.sessions[addr])

    async def _handle_focus_change(self, address: str) -> None:
        """Handle activewindowv2 event — update focused session."""
        addr = normalize_address(address)
        old_addr = self._focused_address
        self._focused_address = addr

        # Clear old focus
        if old_addr and old_addr in self.sessions:
            self.sessions[old_addr].is_focused = False
            if self.on_session_update:
                await self.on_session_update(self.sessions[old_addr])

        # Set new focus
        if addr in self.sessions:
            self.sessions[addr].is_focused = True
            if self.on_session_update:
                await self.on_session_update(self.sessions[addr])

    def _handle_title_change(self, address: str, title: str) -> None:
        """Handle windowtitlev2 event — update/create/remove session."""
        addr = normalize_address(address)
        meta = self._window_meta.get(addr)
        if meta is None:
            return

        window_class = meta["class"]
        workspace_id = meta["workspace_id"]
        parsed = parse_window_title(title, window_class)

        if parsed is None:
            # Title no longer matches Claude — keep existing session
            # (e.g., Zellij pane switched away from the Claude pane)
            return

        if not _is_valid_workspace(workspace_id):
            return

        if addr in self.sessions:
            # Update existing session in place
            session = self.sessions[addr]
            session.session_name = parsed["session_name"]
            session.task_description = parsed["task_description"]
            session.state = parsed["state"]
            session.status_char = parsed["status_char"]
        else:
            # Create new session
            self.sessions[addr] = AgentSession(
                address=addr,
                session_name=parsed["session_name"],
                task_description=parsed["task_description"],
                state=parsed["state"],
                workspace_id=workspace_id,
                window_class=window_class,
                status_char=parsed["status_char"],
                pid=meta.get("pid"),
            )

    def _handle_window_open(self, event: dict) -> None:
        """Handle openwindow event — register window and possibly create session."""
        addr = normalize_address(event["address"])
        workspace_id = event["workspace_id"]
        window_class = event["window_class"]
        title = event["title"]

        self._window_meta[addr] = {
            "class": window_class,
            "workspace_id": workspace_id,
            "pid": None,  # Not available from openwindow event; backfilled on refresh
        }

        if not _is_valid_workspace(workspace_id):
            return

        parsed = parse_window_title(title, window_class)
        if parsed is None:
            return

        self.sessions[addr] = AgentSession(
            address=addr,
            session_name=parsed["session_name"],
            task_description=parsed["task_description"],
            state=parsed["state"],
            workspace_id=workspace_id,
            window_class=window_class,
            status_char=parsed["status_char"],
            pid=None,
        )

    def _handle_window_close(self, address: str) -> None:
        """Handle closewindow event — clean up window and session."""
        addr = normalize_address(address)
        self._window_meta.pop(addr, None)
        self.sessions.pop(addr, None)

    def _handle_window_move(self, address: str, workspace_id: int) -> None:
        """Handle movewindowv2 event — update workspace group."""
        addr = normalize_address(address)
        meta = self._window_meta.get(addr)
        if meta is None:
            return

        meta["workspace_id"] = workspace_id

        if addr in self.sessions:
            if _is_valid_workspace(workspace_id):
                session = self.sessions[addr]
                session.workspace_id = workspace_id
                session.workspace_group = workspace_id % 10
            else:
                # Moved to invalid workspace group — remove session
                self.sessions.pop(addr, None)
