"""Zellij session helpers."""

from __future__ import annotations

import os
import re
import shutil
import shlex
import subprocess


def middle_workspace_for_group(group: int) -> int:
    """Return the middle workspace id for a 1-9 workspace group."""
    if group < 1 or group > 9:
        raise ValueError("workspace group must be 1-9")
    return group + 10


def session_name_for_run_id(run_id: str) -> str:
    """Build a stable zellij session name from an agent run id."""
    name = run_id.removesuffix("::main")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return name[:80] or "agent-monitor"


def zellij_attach_command(
    session_name: str,
    *,
    create: bool = False,
    cwd: str | None = None,
) -> list[str]:
    """Build a zellij attach command."""
    command = ["zellij", "attach"]
    if create:
        command.append("--create")
    command.append(session_name)
    if cwd:
        command.extend(["options", "--default-cwd", cwd])
    return command


def terminal_attach_command(
    session_name: str,
    terminal: str | None = None,
    *,
    create: bool = False,
    cwd: str | None = None,
) -> list[str] | None:
    """Build a terminal command that attaches to a zellij session."""
    zellij_command = zellij_attach_command(session_name, create=create, cwd=cwd)
    terminal = terminal or os.environ.get("AGENT_MONITOR_TERMINAL")
    if terminal:
        return _terminal_command(terminal, zellij_command)

    for candidate in ("ghostty", "kitty", "alacritty", "foot", "wezterm"):
        if shutil.which(candidate):
            return _terminal_command(candidate, zellij_command)
    return None


def attach_session(
    session_name: str,
    workspace_group: int | None = None,
    *,
    create: bool = False,
    cwd: str | None = None,
) -> bool:
    """Open a local terminal attached to a zellij session."""
    command = terminal_attach_command(session_name, create=create, cwd=cwd)
    if command is None:
        return False
    if workspace_group is not None and shutil.which("hyprctl"):
        workspace_id = middle_workspace_for_group(workspace_group)
        subprocess.Popen(
            [
                "hyprctl",
                "dispatch",
                "exec",
                f"[workspace {workspace_id}] {shlex.join(command)}",
            ],
            start_new_session=True,
        )
        return True

    subprocess.Popen(command, start_new_session=True)
    return True


def _terminal_command(terminal: str, command: list[str]) -> list[str]:
    executable = os.path.basename(terminal)
    if executable == "wezterm":
        return [terminal, "start", "--", *command]
    if executable in {"ghostty", "alacritty", "foot"}:
        return [terminal, "-e", *command]
    return [terminal, *command]
