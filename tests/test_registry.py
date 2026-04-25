"""Tests for v2 registry and overlay loading."""

import json
from unittest.mock import patch

from agent_monitor.models import AgentRun, AgentStatus, ClientName
from agent_monitor.registry import (
    build_host_snapshot,
    read_devtools_worktrees,
    read_overlay_agent_runs,
    set_overlay_workspace_group,
    set_overlay_zellij_session,
    write_overlay_agent_runs,
)


def test_read_devtools_worktrees(tmp_path):
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({
        "instances": {
            "game-engine-v2::combat-ui": {
                "branch": "combat-ui",
                "port": 4030,
                "tidewave_port": 9860,
                "mcp_name": "tidewave-game-engine-v2-combat-ui",
                "worktree_path": ".worktrees/combat-ui",
                "project_root": "/home/jesse/projects/game-engine-v2",
                "containerized": True,
                "created_at": "2026-04-19T00:00:00Z",
            }
        }
    }))

    worktrees = read_devtools_worktrees(path)

    assert len(worktrees) == 1
    assert worktrees[0].id == "game-engine-v2::combat-ui"
    assert worktrees[0].project == "game-engine-v2"
    assert worktrees[0].path == "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui"


def test_read_overlay_agent_runs(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "agent_runs": {
            "game-engine-v2::combat-ui::main": {
                "worktree_id": "game-engine-v2::combat-ui",
                "client": "codex",
                "workspace_group": 3,
                "zellij_session": "ge2-combat-ui",
                "agent_pane": "agent",
                "cwd": "/home/jesse/projects/game-engine-v2/.worktrees/combat-ui",
                "client_ids": {"codex_thread_id": None},
                "launch": {"argv": ["codex", "--cd", "/repo/.worktrees/combat-ui"]},
            }
        }
    }))

    runs = read_overlay_agent_runs(path)

    assert len(runs) == 1
    assert runs[0].client == ClientName.CODEX
    assert runs[0].status == AgentStatus.STOPPED
    assert runs[0].workspace_group == 3


def test_write_overlay_agent_runs_round_trip(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "agent_runs": {
            "project::branch::main": {
                "worktree_id": "project::branch",
                "client": "claude",
                "workspace_group": 7,
            }
        }
    }))
    destination = tmp_path / "agent-monitor" / "sessions.json"

    runs = read_overlay_agent_runs(source)
    write_overlay_agent_runs(runs, destination)
    round_trip = read_overlay_agent_runs(destination)

    assert len(round_trip) == 1
    assert round_trip[0].id == "project::branch::main"
    assert round_trip[0].workspace_group == 7


def test_set_overlay_workspace_group_upserts_run(tmp_path):
    path = tmp_path / "sessions.json"
    run = read_overlay_agent_runs(path)
    assert run == []

    updated = set_overlay_workspace_group(
        run=AgentRun(
            id="project::branch::main",
            worktree_id="project::branch",
            client=ClientName.CODEX,
            cwd="/repo/project/.worktrees/branch",
        ),
        workspace_group=5,
        path=path,
    )

    assert updated.workspace_group == 5
    runs = read_overlay_agent_runs(path)
    assert len(runs) == 1
    assert runs[0].id == "project::branch::main"
    assert runs[0].workspace_group == 5
    assert runs[0].client == ClientName.CODEX


def test_set_overlay_workspace_group_updates_existing_run(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "agent_runs": {
            "project::branch::main": {
                "worktree_id": "project::branch",
                "client": "codex",
                "workspace_group": 2,
                "zellij_session": "project-branch",
            }
        }
    }))
    run = read_overlay_agent_runs(path)[0]

    run.zellij_session = "fresh-session"
    run.cwd = "/repo/project/.worktrees/branch"

    set_overlay_workspace_group(run, 8, path)

    runs = read_overlay_agent_runs(path)
    assert runs[0].workspace_group == 8
    assert runs[0].zellij_session == "fresh-session"
    assert runs[0].cwd == "/repo/project/.worktrees/branch"


def test_set_overlay_zellij_session_upserts_run(tmp_path):
    path = tmp_path / "sessions.json"
    updated = set_overlay_zellij_session(
        run=AgentRun(
            id="project::branch::main",
            worktree_id="project::branch",
            client=ClientName.CODEX,
            cwd="/repo/project/.worktrees/branch",
        ),
        zellij_session="project-branch",
        path=path,
    )

    assert updated.zellij_session == "project-branch"
    runs = read_overlay_agent_runs(path)
    assert len(runs) == 1
    assert runs[0].zellij_session == "project-branch"
    assert runs[0].cwd == "/repo/project/.worktrees/branch"


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_adds_stopped_runs_for_worktrees(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::a": {
                "branch": "a",
                "worktree_path": ".worktrees/a",
                "project_root": "/repo/project",
            },
            "project::b": {
                "branch": "b",
                "worktree_path": ".worktrees/b",
                "project_root": "/repo/project",
            },
        }
    }))
    overlay_path.write_text(json.dumps({
        "agent_runs": {
            "project::a::main": {
                "worktree_id": "project::a",
                "client": "codex",
            }
        }
    }))

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
    )

    assert snapshot.host.name == "test-host"
    assert snapshot.host.hyprland is False
    assert [worktree.id for worktree in snapshot.worktrees] == ["project::a", "project::b"]
    assert {run.id for run in snapshot.agent_runs} == {"project::a::main", "project::b::main"}
    synthetic = next(run for run in snapshot.agent_runs if run.id == "project::b::main")
    assert synthetic.status == AgentStatus.STOPPED
    assert synthetic.cwd == "/repo/project/.worktrees/b"


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes")
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_marks_matching_codex_worktree_running(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::a": {
                "branch": "a",
                "worktree_path": ".worktrees/a",
                "project_root": "/repo/project",
            },
            "project::b": {
                "branch": "b",
                "worktree_path": ".worktrees/b",
                "project_root": "/repo/project",
            },
        }
    }))
    overlay_path.write_text(json.dumps({"agent_runs": {}}))
    mock_processes.return_value = [
        {
            "pid": 123,
            "cwd": "/repo/project/.worktrees/a",
            "zellij_session_name": "project-a",
        }
    ]

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
    )

    runs = {run.worktree_id: run for run in snapshot.agent_runs}
    assert runs["project::a"].client == ClientName.CODEX
    assert runs["project::a"].status == AgentStatus.RUNNING
    assert runs["project::a"].zellij_session == "project-a"
    assert runs["project::b"].status == AgentStatus.STOPPED


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes")
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_preserves_overlay_metadata_when_codex_running(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::a": {
                "branch": "a",
                "worktree_path": ".worktrees/a",
                "project_root": "/repo/project",
            }
        }
    }))
    overlay_path.write_text(json.dumps({
        "agent_runs": {
            "project::a::main": {
                "worktree_id": "project::a",
                "client": "codex",
                "workspace_group": 4,
            }
        }
    }))
    mock_processes.return_value = [
        {
            "pid": 123,
            "cwd": "/repo/project/.worktrees/a/src",
            "zellij_session_name": None,
        }
    ]

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
    )

    assert len(snapshot.agent_runs) == 1
    run = snapshot.agent_runs[0]
    assert run.id == "project::a::main"
    assert run.workspace_group == 4
    assert run.status == AgentStatus.RUNNING
    assert run.cwd == "/repo/project/.worktrees/a/src"


def test_missing_registry_files_are_empty(tmp_path):
    assert read_devtools_worktrees(tmp_path / "missing-instances.json") == []
    assert read_overlay_agent_runs(tmp_path / "missing-sessions.json") == []
