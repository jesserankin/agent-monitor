"""Zellij session helpers."""

from __future__ import annotations

from collections.abc import Sequence
import json
import os
import re
import shutil
import shlex
import subprocess

from agent_monitor.workspace import workspace_id_for_group

CONTEXT_USED_TITLE_RE = re.compile(r"\bContext\s+(\d+(?:\.\d+)?)%\s+used\b", re.IGNORECASE)


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


def zellij_create_background_command(
    session_name: str,
    *,
    cwd: str | None = None,
) -> list[str]:
    """Build a command that ensures a detached zellij session exists."""
    command = ["zellij", "attach", "--create-background", session_name]
    if cwd:
        command.extend(["options", "--default-cwd", cwd])
    return command


def zellij_run_command(
    session_name: str,
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    pane_name: str | None = None,
) -> list[str]:
    """Build a command that runs argv inside an existing zellij session."""
    command = ["zellij", "--session", session_name, "run"]
    if pane_name:
        command.extend(["--name", pane_name])
    if cwd:
        command.extend(["--cwd", cwd])
    command.extend(["--", *argv])
    return command


def zellij_list_panes_command(session_name: str) -> list[str]:
    """Build a command that lists panes for a zellij session as JSON."""
    return ["zellij", "--session", session_name, "action", "list-panes", "--json"]


def zellij_list_sessions_command() -> list[str]:
    """Build a command that lists active zellij session names."""
    return ["zellij", "list-sessions", "--short", "--no-formatting"]


def list_sessions() -> list[str]:
    """Read active zellij session names, returning an empty list when unavailable."""
    try:
        result = subprocess.run(
            zellij_list_sessions_command(),
            capture_output=True,
            check=True,
            timeout=2.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    output = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else str(result.stdout)
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def list_panes(session_name: str) -> list[dict]:
    """Read pane metadata from zellij, returning an empty list when unavailable."""
    try:
        result = subprocess.run(
            zellij_list_panes_command(session_name),
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


def context_used_pct_from_panes(panes: Sequence[dict]) -> float | None:
    """Extract Codex context usage from zellij pane titles."""
    candidates = sorted(
        (pane for pane in panes if not pane.get("is_plugin")),
        key=lambda pane: 0 if pane.get("is_focused") else 1,
    )
    for pane in candidates:
        title = pane.get("title")
        if not isinstance(title, str):
            continue
        match = CONTEXT_USED_TITLE_RE.search(title)
        if match:
            return _clamp_pct(float(match.group(1)))
    return None


def read_context_used_pct_from_pane_titles(session_name: str) -> float | None:
    """Read Codex context usage from zellij pane titles when list-panes exists."""
    return context_used_pct_from_panes(list_panes(session_name))


def create_session_with_command(
    session_name: str,
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    pane_name: str | None = None,
) -> bool:
    """Create a detached session, then start argv in it."""
    if not argv:
        return False
    try:
        subprocess.run(
            zellij_create_background_command(session_name, cwd=cwd),
            capture_output=True,
            check=True,
            timeout=10,
        )
        subprocess.run(
            zellij_run_command(session_name, argv, cwd=cwd, pane_name=pane_name),
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def ensure_session(
    session_name: str,
    *,
    cwd: str | None = None,
    launch_argv: Sequence[str] | None = None,
    pane_name: str | None = None,
) -> bool:
    """Ensure a zellij session exists without opening a terminal attach client."""
    if session_name in list_sessions():
        return True
    if launch_argv:
        return create_session_with_command(
            session_name,
            launch_argv,
            cwd=cwd,
            pane_name=pane_name,
        )
    try:
        subprocess.run(
            zellij_create_background_command(session_name, cwd=cwd),
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def terminal_attach_command(
    session_name: str,
    terminal: str | None = None,
    *,
    create: bool = False,
    cwd: str | None = None,
) -> list[str] | None:
    """Build a terminal command that attaches to a zellij session."""
    zellij_command = zellij_attach_command(session_name, create=create, cwd=cwd)
    return terminal_command(zellij_command, terminal=terminal)


def terminal_command(command: list[str], terminal: str | None = None) -> list[str] | None:
    """Build a terminal command that runs an arbitrary argv."""
    terminal = terminal or os.environ.get("AGENT_MONITOR_TERMINAL")
    if terminal:
        return _terminal_command(terminal, command)

    for candidate in ("ghostty", "kitty", "alacritty", "foot", "wezterm"):
        if shutil.which(candidate):
            return _terminal_command(candidate, command)
    return None


def attach_session(
    session_name: str,
    workspace_group: int | None = None,
    *,
    create: bool = False,
    cwd: str | None = None,
    launch_argv: Sequence[str] | None = None,
    pane_name: str | None = None,
) -> bool:
    """Open a local terminal attached to a zellij session."""
    command = terminal_attach_command(
        session_name,
        create=create and not launch_argv,
        cwd=cwd,
    )
    if command is None:
        return False

    if create and launch_argv:
        if not create_session_with_command(
            session_name,
            launch_argv,
            cwd=cwd,
            pane_name=pane_name,
        ):
            return False

    if workspace_group is not None and shutil.which("hyprctl"):
        workspace_id = workspace_id_for_group(workspace_group)
        subprocess.Popen(
            [
                "hyprctl",
                "dispatch",
                "exec",
                f"[workspace {workspace_id}] {shlex.join(command)}",
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    subprocess.Popen(
        command,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def _terminal_command(terminal: str, command: list[str]) -> list[str]:
    executable = os.path.basename(terminal)
    if executable == "wezterm":
        return [terminal, "start", "--", *command]
    if executable in {"ghostty", "alacritty", "foot"}:
        return [terminal, "-e", *command]
    return [terminal, *command]


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, value))
