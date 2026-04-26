"""Tests for host adapters."""

import sys
from unittest.mock import patch

from agent_monitor.hosts import LocalHostAdapter
from agent_monitor.models import AgentRun, ClientName
from agent_monitor.registry import read_overlay_agent_runs


def test_local_host_adapter_launches_codex_for_new_codex_session(tmp_path):
    overlay_path = tmp_path / "sessions.json"
    adapter = LocalHostAdapter(overlay_path=overlay_path)
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        workspace_group=4,
        cwd="/repo/project/.worktrees/branch",
    )

    with patch("agent_monitor.hosts.attach_session", return_value=True) as mock_attach:
        assert adapter.open_run(run) is True

    mock_attach.assert_called_once_with(
        "project-branch",
        workspace_group=4,
        create=True,
        cwd="/repo/project/.worktrees/branch",
        launch_argv=[
            sys.executable,
            "-m",
            "agent_monitor",
            "codex-sidecar",
            "--run-id",
            "project::branch::main",
            "--worktree-id",
            "project::branch",
            "--cwd",
            "/repo/project/.worktrees/branch",
            "--zellij-session",
            "project-branch",
            "--",
            "codex",
            "--cd",
            "/repo/project/.worktrees/branch",
        ],
        pane_name="agent",
    )
    persisted = read_overlay_agent_runs(overlay_path)
    assert persisted[0].zellij_session == "project-branch"


def test_local_host_adapter_uses_persisted_launch_command_for_new_session(tmp_path):
    adapter = LocalHostAdapter(overlay_path=tmp_path / "sessions.json")
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CLAUDE,
        cwd="/repo/project/.worktrees/branch",
        launch={"argv": ["claude", "--dangerously-skip-permissions"]},
    )

    with patch("agent_monitor.hosts.attach_session", return_value=True) as mock_attach:
        assert adapter.open_run(run) is True

    assert mock_attach.call_args.kwargs["launch_argv"] == ["claude", "--dangerously-skip-permissions"]


def test_local_host_adapter_wraps_persisted_codex_launch_command(tmp_path):
    adapter = LocalHostAdapter(overlay_path=tmp_path / "sessions.json")
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        cwd="/repo/project/.worktrees/branch",
        launch={"argv": ["codex", "--cd", "/custom/path"]},
    )

    with patch("agent_monitor.hosts.attach_session", return_value=True) as mock_attach:
        assert adapter.open_run(run) is True

    assert mock_attach.call_args.kwargs["launch_argv"] == [
        sys.executable,
        "-m",
        "agent_monitor",
        "codex-sidecar",
        "--run-id",
        "project::branch::main",
        "--worktree-id",
        "project::branch",
        "--cwd",
        "/repo/project/.worktrees/branch",
        "--zellij-session",
        "project-branch",
        "--",
        "codex",
        "--cd",
        "/custom/path",
    ]


def test_local_host_adapter_does_not_launch_command_for_existing_session(tmp_path):
    adapter = LocalHostAdapter(overlay_path=tmp_path / "sessions.json")
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        zellij_session="project-branch",
        cwd="/repo/project/.worktrees/branch",
    )

    with patch("agent_monitor.hosts.attach_session", return_value=True) as mock_attach:
        assert adapter.open_run(run) is True

    mock_attach.assert_called_once_with(
        "project-branch",
        workspace_group=None,
        create=False,
        cwd="/repo/project/.worktrees/branch",
        launch_argv=None,
        pane_name="agent",
    )
