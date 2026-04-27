"""SSH transport helpers for remote agent-monitor hosts."""

from __future__ import annotations

from collections.abc import Sequence
import json
import shlex
import shutil
import subprocess

from agent_monitor.workspace import workspace_id_for_group
from agent_monitor.zellij import terminal_command


class SshCommandError(RuntimeError):
    """Raised when a remote agent-monitor command fails."""


class SshTransport:
    """Bounded subprocess transport for remote agent-monitor helper commands."""

    def __init__(
        self,
        host: str,
        *,
        agent_monitor_command: str = "agent-monitor",
        ssh_command: str = "ssh",
        timeout: float = 15.0,
    ) -> None:
        self.host = host
        self.agent_monitor_command = agent_monitor_command
        self.ssh_command = ssh_command
        self.timeout = timeout

    def command(self, args: Sequence[str]) -> list[str]:
        return [self.ssh_command, self.host, self.agent_monitor_command, *args]

    def run_json(self, args: Sequence[str]) -> dict:
        try:
            result = subprocess.run(
                self.command(args),
                capture_output=True,
                check=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SshCommandError(f"ssh command failed for {self.host}: {exc}") from exc

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SshCommandError(f"remote command returned invalid JSON for {self.host}") from exc
        if not isinstance(data, dict):
            raise SshCommandError(f"remote command returned non-object JSON for {self.host}")
        return data


def ssh_zellij_attach_command(host: str, session_name: str) -> list[str]:
    return ["ssh", "-t", host, "zellij", "attach", session_name]


def open_ssh_zellij_attach(
    host: str,
    session_name: str,
    *,
    workspace_group: int | None = None,
    terminal: str | None = None,
) -> bool:
    """Open a local terminal that attaches to a remote zellij session over SSH."""
    command = terminal_command(ssh_zellij_attach_command(host, session_name), terminal=terminal)
    if command is None:
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
