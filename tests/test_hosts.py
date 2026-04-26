"""Tests for host adapters."""

import json
import sys
from unittest.mock import patch

from agent_monitor.config import RemoteHostConfig
from agent_monitor.hosts import LocalHostAdapter, MultiHostAdapter, SshHostAdapter
from agent_monitor.models import AgentRun, ClientName, HostInfo, HostSnapshot, Worktree
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

    assert adapter.last_open_action == "created_session"
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


def test_local_host_adapter_passes_known_codex_thread_to_sidecar(tmp_path):
    adapter = LocalHostAdapter(overlay_path=tmp_path / "sessions.json")
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        cwd="/repo/project/.worktrees/branch",
        client_ids={"codex_thread_id": "thread-123"},
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
        "--codex-thread-id",
        "thread-123",
        "--",
        "codex",
        "--cd",
        "/repo/project/.worktrees/branch",
    ]


def test_local_host_adapter_launches_codex_in_devcontainer_for_containerized_worktree(tmp_path):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    project_root = tmp_path / "project"
    worktree_path = project_root / ".worktrees" / "branch"
    devcontainer_dir = project_root / ".devcontainer"
    devcontainer_dir.mkdir(parents=True)
    worktree_path.mkdir(parents=True)
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps({
        "workspaceFolder": "/workspace"
    }))
    registry_path.write_text(json.dumps({
        "instances": {
            "project::branch": {
                "branch": "branch",
                "worktree_path": str(worktree_path),
                "project_root": str(project_root),
                "containerized": True,
            }
        }
    }))
    adapter = LocalHostAdapter(devtools_registry_path=registry_path, overlay_path=overlay_path)
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        cwd=str(worktree_path),
    )

    with patch("agent_monitor.hosts.subprocess.run") as mock_run, \
         patch("agent_monitor.hosts.attach_session", return_value=True) as mock_attach:
        assert adapter.open_run(run) is True

    mock_run.assert_called_once_with(
        ["devcontainer", "up", "--workspace-folder", str(project_root)],
        capture_output=True,
        check=True,
        timeout=120,
    )
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
        str(worktree_path),
        "--zellij-session",
        "project-branch",
        "--",
        "devcontainer",
        "exec",
        "--workspace-folder",
        str(project_root),
        "sh",
        "-lc",
        "cd /workspace/.worktrees/branch && exec codex --cd /workspace/.worktrees/branch",
    ]


def test_local_host_adapter_fails_devcontainer_run_when_container_cannot_start(tmp_path):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    project_root = tmp_path / "project"
    worktree_path = project_root / ".worktrees" / "branch"
    registry_path.write_text(json.dumps({
        "instances": {
            "project::branch": {
                "branch": "branch",
                "worktree_path": str(worktree_path),
                "project_root": str(project_root),
                "containerized": True,
            }
        }
    }))
    adapter = LocalHostAdapter(devtools_registry_path=registry_path, overlay_path=overlay_path)
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        cwd=str(worktree_path),
    )

    with patch("agent_monitor.hosts.subprocess.run", side_effect=OSError("missing")), \
         patch("agent_monitor.hosts.attach_session") as mock_attach:
        assert adapter.open_run(run) is False

    mock_attach.assert_not_called()


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

    assert adapter.last_open_action == "opened_terminal"
    mock_attach.assert_called_once_with(
        "project-branch",
        workspace_group=None,
        create=False,
        cwd="/repo/project/.worktrees/branch",
        launch_argv=None,
        pane_name="agent",
    )


def test_local_host_adapter_focuses_existing_zellij_window_instead_of_duplicate_attach(tmp_path):
    adapter = LocalHostAdapter(overlay_path=tmp_path / "sessions.json")
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        workspace_group=4,
        zellij_session="project-branch",
        cwd="/repo/project/.worktrees/branch",
    )

    with patch("agent_monitor.hosts.find_zellij_window_sync", return_value={"address": "abc123", "workspace_id": 11}), \
         patch("agent_monitor.hosts.switch_to_group_sync", return_value=True) as mock_switch, \
         patch("agent_monitor.hosts.move_window_to_workspace", return_value=True) as mock_move, \
         patch("agent_monitor.hosts.focus_window_sync", return_value=True) as mock_focus, \
         patch("agent_monitor.hosts.attach_session") as mock_attach:
        assert adapter.open_run(run) is True

    assert adapter.last_open_action == "focused_existing_window"
    mock_switch.assert_called_once_with(4)
    mock_move.assert_called_once_with("abc123", 14)
    mock_focus.assert_called_once_with("abc123")
    mock_attach.assert_not_called()


def test_local_host_adapter_set_group_moves_existing_zellij_window(tmp_path):
    overlay_path = tmp_path / "sessions.json"
    adapter = LocalHostAdapter(overlay_path=overlay_path)
    run = AgentRun(
        id="project::branch::main",
        worktree_id="project::branch",
        client=ClientName.CODEX,
        zellij_session="project-branch",
        cwd="/repo/project/.worktrees/branch",
    )

    with patch("agent_monitor.hosts.find_zellij_window_sync", return_value={"address": "abc123", "workspace_id": 11}), \
         patch("agent_monitor.hosts.move_window_to_workspace", return_value=True) as mock_move:
        updated = adapter.set_workspace_group(run, 5)

    assert updated.workspace_group == 5
    mock_move.assert_called_once_with("abc123", 15)


class FakeSshTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run_json(self, args):
        self.calls.append(args)
        return self.responses.pop(0)


def test_ssh_host_adapter_reads_remote_snapshot_with_configured_host_identity():
    transport = FakeSshTransport([
        {
            "host": {"name": "actual-hostname", "transport": "local", "hyprland": True},
            "worktrees": [
                {
                    "id": "project::branch",
                    "project": "project",
                    "branch": "branch",
                    "path": "/repo/.worktrees/branch",
                }
            ],
            "agent_runs": [],
        }
    ])
    adapter = SshHostAdapter(
        RemoteHostConfig(name="workstation", host="jesse@workstation.local"),
        transport=transport,
    )

    snapshot = adapter.snapshot()

    assert transport.calls == [["host-snapshot", "--json"]]
    assert snapshot.host.name == "workstation"
    assert snapshot.host.transport == "ssh"
    assert snapshot.host.hyprland is True
    assert snapshot.worktrees[0].id == "project::branch"


def test_ssh_host_adapter_set_group_delegates_to_remote_helper():
    transport = FakeSshTransport([
        {
            "ok": True,
            "command": "set-group",
            "run": {
                "id": "project::branch::main",
                "worktree_id": "project::branch",
                "client": "codex",
                "workspace_group": 8,
            },
        }
    ])
    adapter = SshHostAdapter(RemoteHostConfig(name="workstation", host="ssh-host"), transport=transport)
    run = AgentRun(id="project::branch::main", worktree_id="project::branch", client=ClientName.CODEX)

    updated = adapter.set_workspace_group(run, 8)

    assert transport.calls == [["set-group", "project::branch::main", "8", "--json"]]
    assert updated.workspace_group == 8
    assert updated.client == ClientName.CODEX


def test_ssh_host_adapter_open_run_calls_remote_helper_and_opens_local_ssh_attach():
    transport = FakeSshTransport([
        {
            "ok": True,
            "command": "open-run",
            "action": "opened_terminal",
            "run": {
                "id": "project::branch::main",
                "worktree_id": "project::branch",
                "client": "codex",
                "workspace_group": 4,
                "zellij_session": "project-branch",
            },
        }
    ])
    adapter = SshHostAdapter(RemoteHostConfig(name="workstation", host="ssh-host"), transport=transport)
    run = AgentRun(id="project::branch::main", worktree_id="project::branch", client=ClientName.CODEX)

    with patch("agent_monitor.hosts.open_ssh_zellij_attach", return_value=True) as mock_attach:
        assert adapter.open_run(run) is True

    assert transport.calls == [["open-run", "project::branch::main", "--json"]]
    mock_attach.assert_called_once_with("ssh-host", "project-branch", workspace_group=4)
    assert adapter.last_open_action == "opened_ssh_terminal"


def test_multi_host_adapter_merges_snapshots_and_routes_actions_to_owning_adapter():
    local_run = AgentRun(id="local::branch::main", worktree_id="local::branch")
    remote_run = AgentRun(id="remote::branch::main", worktree_id="remote::branch")
    local = _StaticAdapter(
        HostSnapshot(
            host=HostInfo(name="local"),
            worktrees=[Worktree(id="local::branch", project="local", branch="branch", path="/local")],
            agent_runs=[local_run],
        )
    )
    remote = _StaticAdapter(
        HostSnapshot(
            host=HostInfo(name="workstation", transport="ssh"),
            worktrees=[Worktree(id="remote::branch", project="remote", branch="branch", path="/remote")],
            agent_runs=[remote_run],
        )
    )
    adapter = MultiHostAdapter([local, remote])

    snapshot = adapter.snapshot()
    adapter.open_run(snapshot.agent_runs[1])
    adapter.set_workspace_group(snapshot.agent_runs[1], 7)

    assert snapshot.host.name == "local, workstation"
    assert [run.id for run in snapshot.agent_runs] == ["local::branch::main", "remote::branch::main"]
    assert local.opened == []
    assert remote.opened == ["remote::branch::main"]
    assert remote.assigned == [("remote::branch::main", 7)]


class _StaticAdapter:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.opened = []
        self.assigned = []
        self.last_open_action = None

    def snapshot(self):
        return self._snapshot

    def open_run(self, run):
        self.opened.append(run.id)
        self.last_open_action = "opened"
        return True

    def set_workspace_group(self, run, workspace_group):
        self.assigned.append((run.id, workspace_group))
        run.workspace_group = workspace_group
        return run
