"""Workspace switching via Hyprland and workspace-group script."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

logger = logging.getLogger(__name__)


def workspace_id_for_group(group: int) -> int:
    """Return a local Hyprland workspace id for a logical workspace group.

    Workspace groups are stored as 1-9, but the concrete workspace id depends on
    the local monitor layout. On the three-monitor setup the middle monitor uses
    11-19; on a single laptop panel it is usually 1-9.
    """
    if not 1 <= group <= 9:
        raise ValueError(f"Workspace group must be 1-9, got {group}")

    base = _workspace_base_for_current_monitors(_fetch_monitors_sync())
    if base is None:
        base = 10
    return base + group


async def switch_to_group(group: int) -> None:
    """Switch to a Hyprland workspace group (1-9).

    Runs the ``workspace-group`` script. Logs warnings on failure
    but never raises (except ValueError for invalid group).
    """
    if not 1 <= group <= 9:
        raise ValueError(f"Workspace group must be 1-9, got {group}")

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "workspace-group", str(group),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except asyncio.TimeoutError:
        logger.warning("workspace-group %d timed out", group)
        if proc is not None:
            proc.kill()
            await proc.communicate()
        return
    except FileNotFoundError:
        logger.warning("workspace-group not found on PATH")
        return

    if proc.returncode != 0:
        logger.warning(
            "workspace-group %d exited with code %d: %s",
            group, proc.returncode, stderr.decode(),
        )


def switch_to_group_sync(group: int) -> bool:
    """Switch to a Hyprland workspace group for synchronous callers."""
    if not 1 <= group <= 9:
        raise ValueError(f"Workspace group must be 1-9, got {group}")

    try:
        subprocess.run(
            ["workspace-group", str(group)],
            capture_output=True,
            check=True,
            timeout=3.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.warning("workspace-group %d failed", group)
        return False
    return True


def _fetch_monitors_sync() -> list[dict]:
    try:
        result = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True,
            check=True,
            timeout=2.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _workspace_base_for_current_monitors(monitors: list[dict]) -> int | None:
    bases: list[int] = []
    focused_base: int | None = None
    for monitor in monitors:
        if monitor.get("disabled") is True:
            continue
        base = _workspace_base_for_monitor(monitor)
        if base is None:
            continue
        if base not in bases:
            bases.append(base)
        if monitor.get("focused") is True:
            focused_base = base

    if not bases:
        return None
    if len(bases) == 1:
        return bases[0]
    if 10 in bases:
        return 10
    if focused_base is not None:
        return focused_base
    return bases[0]


def _workspace_base_for_monitor(monitor: dict) -> int | None:
    active_workspace = monitor.get("activeWorkspace")
    if not isinstance(active_workspace, dict):
        return None
    workspace_id = active_workspace.get("id")
    if not isinstance(workspace_id, int) or workspace_id <= 0 or workspace_id % 10 == 0:
        return None
    return (workspace_id // 10) * 10


async def focus_window(address: str) -> None:
    """Focus a specific Hyprland window by address.

    Runs ``hyprctl dispatch focuswindow address:0x{address}``.
    Logs warnings on failure but never raises.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "hyprctl", "dispatch", "focuswindow", f"address:0x{address}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except asyncio.TimeoutError:
        logger.warning("hyprctl dispatch focuswindow timed out")
        if proc is not None:
            proc.kill()
            await proc.communicate()
        return
    except FileNotFoundError:
        logger.warning("hyprctl not found on PATH")
        return

    if proc.returncode != 0:
        logger.warning(
            "hyprctl dispatch focuswindow exited with code %d: %s",
            proc.returncode, stderr.decode(),
        )


def move_window_to_workspace(address: str, workspace_id: int) -> bool:
    """Move a Hyprland window to a workspace by address."""
    if workspace_id <= 0:
        raise ValueError(f"Workspace id must be positive, got {workspace_id}")
    address = _normalize_address(address)
    try:
        subprocess.run(
            ["hyprctl", "dispatch", "movetoworkspacesilent", f"{workspace_id},address:0x{address}"],
            capture_output=True,
            check=True,
            timeout=3.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.warning("hyprctl dispatch movetoworkspacesilent failed for %s", address)
        return False
    return True


def focus_window_sync(address: str) -> bool:
    """Focus a Hyprland window by address for synchronous callers."""
    address = _normalize_address(address)
    try:
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"address:0x{address}"],
            capture_output=True,
            check=True,
            timeout=3.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.warning("hyprctl dispatch focuswindow failed for %s", address)
        return False
    return True


def _normalize_address(address: str) -> str:
    if address.startswith("0x"):
        return address[2:]
    return address
