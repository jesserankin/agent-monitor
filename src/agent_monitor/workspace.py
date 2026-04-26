"""Workspace switching via Hyprland and workspace-group script."""

from __future__ import annotations

import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)


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
