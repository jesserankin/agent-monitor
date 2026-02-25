"""Process tree helpers for Linux /proc filesystem.

Used to correlate terminal windows with Claude processes via Zellij session names.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_environ_var(pid: int, var: str) -> str | None:
    """Read a single environment variable from /proc/<pid>/environ."""
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except (OSError, PermissionError):
        return None
    for entry in data.split(b"\0"):
        if entry.startswith(var.encode() + b"="):
            return entry.split(b"=", 1)[1].decode(errors="replace")
    return None


def _get_child_pids(pid: int) -> list[int]:
    """Get direct child PIDs of a process via /proc/<pid>/task/*/children."""
    children: list[int] = []
    task_dir = Path(f"/proc/{pid}/task")
    try:
        for tid_dir in task_dir.iterdir():
            children_file = tid_dir / "children"
            try:
                text = children_file.read_text().strip()
                if text:
                    children.extend(int(c) for c in text.split())
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return children


def _get_ppid(pid: int) -> int | None:
    """Get parent PID from /proc/<pid>/status."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split(":")[1].strip())
    except (OSError, ValueError):
        pass
    return None


def _process_name(pid: int) -> str | None:
    """Read process comm name from /proc/<pid>/comm."""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return None


def _read_cwd(pid: int) -> str | None:
    """Read the current working directory of a process via /proc/<pid>/cwd symlink."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _get_socket_inodes(pid: int) -> set[int]:
    """Get all socket inodes for a process from /proc/<pid>/fd/."""
    inodes: set[int] = set()
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(str(fd))
                m = re.match(r"socket:\[(\d+)\]", target)
                if m:
                    inodes.add(int(m.group(1)))
            except OSError:
                continue
    except OSError:
        pass
    return inodes


def _find_zellij_client_pid(terminal_pid: int) -> int | None:
    """Find the zellij client PID as a child or grandchild of the terminal."""
    children = _get_child_pids(terminal_pid)
    for child in children:
        if _process_name(child) == "zellij":
            return child
    for child in children:
        for grandchild in _get_child_pids(child):
            if _process_name(grandchild) == "zellij":
                return grandchild
    return None


def _build_zellij_socket_map() -> dict[int, str]:
    """Build a mapping from socket inode → zellij session name.

    Uses /proc/net/unix for named zellij server sockets and ss -xpn
    for peer socket matching so we can resolve which session a client
    connects to.

    Returns {socket_inode: session_name} for both server-side and
    client-side inodes of zellij connections.
    """
    # Step 1: Read /proc/net/unix for named zellij socket entries (server-side)
    named_inodes: dict[int, str] = {}  # server socket inode → session name
    try:
        for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) > 7 and "/zellij/" in parts[7]:
                path = parts[7]
                inode = int(parts[6])
                session = path.rsplit("/", 1)[-1]
                named_inodes[inode] = session
    except (OSError, ValueError):
        pass

    if not named_inodes:
        return {}

    # Step 2: Use ss to get peer mappings for these sockets
    # This lets us find which client inodes connect to which server inodes
    inode_to_session: dict[int, str] = dict(named_inodes)

    try:
        result = subprocess.run(
            ["ss", "-xpn"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                m_inode = re.search(r"\*\s+(\d+)\s+\*\s+(\d+)", line)
                if not m_inode:
                    continue
                local = int(m_inode.group(1))
                peer = int(m_inode.group(2))
                # If the peer is a named zellij socket, map the local inode too
                if peer in named_inodes:
                    inode_to_session[local] = named_inodes[peer]
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("ss command failed or timed out")

    return inode_to_session


def find_zellij_session_for_terminal(
    terminal_pid: int,
    socket_map: dict[int, str] | None = None,
) -> str | None:
    """Find the zellij session name for a terminal window.

    Finds the zellij client process (child/grandchild of terminal), gets its
    socket inodes, and matches them against the zellij socket map to determine
    which session the client is connected to.
    """
    client_pid = _find_zellij_client_pid(terminal_pid)
    if client_pid is None:
        return None

    if socket_map is None:
        socket_map = _build_zellij_socket_map()

    client_inodes = _get_socket_inodes(client_pid)
    for inode in client_inodes:
        session = socket_map.get(inode)
        if session:
            return session

    return None


def find_claude_processes() -> list[dict]:
    """Find running Claude Code processes and their CWDs and zellij sessions.

    Returns a list of dicts with keys: pid, cwd, zellij_session_name.
    Finds processes named 'claude' by scanning /proc, then walks ancestors
    to find the zellij server and read its ZELLIJ_SESSION_NAME.
    """
    results: list[dict] = []
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return results

    for pid in pids:
        name = _process_name(pid)
        if name != "claude":
            continue

        cwd = _read_cwd(pid)
        zellij_session = _find_ancestor_zellij_session(pid)

        results.append({
            "pid": pid,
            "cwd": cwd,
            "zellij_session_name": zellij_session,
        })

    return results


def _find_ancestor_zellij_session(pid: int) -> str | None:
    """Walk up the process tree from pid to find a zellij ancestor's session name."""
    visited: set[int] = set()
    current = pid
    while current and current > 1 and current not in visited:
        visited.add(current)
        parent = _get_ppid(current)
        if parent is None or parent <= 1:
            break
        name = _process_name(parent)
        if name == "zellij":
            session = _read_environ_var(parent, "ZELLIJ_SESSION_NAME")
            if session:
                return session
        current = parent
    return None
