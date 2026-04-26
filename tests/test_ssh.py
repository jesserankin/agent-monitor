"""Tests for SSH transport helpers."""

import json
import subprocess
from unittest.mock import patch

import pytest

from agent_monitor.ssh import (
    SshCommandError,
    SshTransport,
    open_ssh_zellij_attach,
    ssh_zellij_attach_command,
)


def test_ssh_transport_runs_agent_monitor_json_command():
    completed = subprocess.CompletedProcess(
        ["ssh"],
        0,
        stdout=json.dumps({"ok": True}),
    )
    transport = SshTransport("workstation", timeout=3.0)

    with patch("agent_monitor.ssh.subprocess.run", return_value=completed) as mock_run:
        assert transport.run_json(["host-snapshot", "--json"]) == {"ok": True}

    mock_run.assert_called_once_with(
        ["ssh", "workstation", "agent-monitor", "host-snapshot", "--json"],
        capture_output=True,
        check=True,
        text=True,
        timeout=3.0,
    )


def test_ssh_transport_raises_for_invalid_json():
    completed = subprocess.CompletedProcess(["ssh"], 0, stdout="not-json")
    transport = SshTransport("workstation")

    with patch("agent_monitor.ssh.subprocess.run", return_value=completed), pytest.raises(SshCommandError):
        transport.run_json(["host-snapshot", "--json"])


def test_ssh_zellij_attach_command():
    assert ssh_zellij_attach_command("workstation", "project-branch") == [
        "ssh",
        "-t",
        "workstation",
        "zellij",
        "attach",
        "project-branch",
    ]


def test_open_ssh_zellij_attach_launches_terminal():
    with patch("agent_monitor.ssh.terminal_command", return_value=["ghostty", "-e", "ssh", "-t", "host", "zellij", "attach", "s"]), \
         patch("agent_monitor.ssh.shutil.which", return_value=None), \
         patch("agent_monitor.ssh.subprocess.Popen") as mock_popen:
        assert open_ssh_zellij_attach("host", "s") is True

    mock_popen.assert_called_once_with(
        ["ghostty", "-e", "ssh", "-t", "host", "zellij", "attach", "s"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_open_ssh_zellij_attach_launches_on_workspace_group():
    with patch("agent_monitor.ssh.terminal_command", return_value=["ghostty", "-e", "ssh", "-t", "host", "zellij", "attach", "s"]), \
         patch("agent_monitor.ssh.shutil.which", return_value="/usr/bin/hyprctl"), \
         patch("agent_monitor.ssh.subprocess.Popen") as mock_popen:
        assert open_ssh_zellij_attach("host", "s", workspace_group=3) is True

    mock_popen.assert_called_once_with(
        [
            "hyprctl",
            "dispatch",
            "exec",
            "[workspace 13] ghostty -e ssh -t host zellij attach s",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
