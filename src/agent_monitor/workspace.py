"""Workspace switching via Hyprland and workspace-group script."""

from __future__ import annotations

import asyncio
import logging

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
