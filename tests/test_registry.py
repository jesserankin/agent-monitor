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
def test_build_host_snapshot_keeps_empty_worktrees_out_of_agent_runs(
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
        sidecar_runs_dir=tmp_path / "missing-runs",
    )

    assert snapshot.host.name == "test-host"
    assert snapshot.host.hyprland is False
    assert [worktree.id for worktree in snapshot.worktrees] == ["project::a", "project::b"]
    assert {run.id for run in snapshot.agent_runs} == {"project::a::main"}


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_can_still_include_legacy_stopped_runs(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::b": {
                "branch": "b",
                "worktree_path": ".worktrees/b",
                "project_root": "/repo/project",
            },
        }
    }))
    overlay_path.write_text(json.dumps({"agent_runs": {}}))

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
        sidecar_runs_dir=tmp_path / "missing-runs",
        include_stopped_worktrees=True,
    )

    assert {run.id for run in snapshot.agent_runs} == {"project::b::main"}
    synthetic = snapshot.agent_runs[0]
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
        sidecar_runs_dir=tmp_path / "missing-runs",
    )

    assert [run.worktree_id for run in snapshot.agent_runs] == ["project::a"]
    run = snapshot.agent_runs[0]
    assert run.client == ClientName.CODEX
    assert run.status == AgentStatus.RUNNING
    assert run.zellij_session == "project-a"


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
        sidecar_runs_dir=tmp_path / "missing-runs",
    )

    assert len(snapshot.agent_runs) == 1
    run = snapshot.agent_runs[0]
    assert run.id == "project::a::main"
    assert run.workspace_group == 4
    assert run.status == AgentStatus.RUNNING
    assert run.cwd == "/repo/project/.worktrees/a/src"


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes")
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_sidecar_status_is_primary_for_codex_run(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    sidecar_dir = tmp_path / "runs"
    sidecar_path = sidecar_dir / "project--a--main" / "status.json"
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
                "workspace_group": 5,
                "model": "gpt-5.5",
                "tokens_used": 9000,
            }
        }
    }))
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_text(json.dumps({
        "run_id": "project::a::main",
        "worktree_id": "project::a",
        "client": "codex",
        "status": "waiting_approval",
        "cwd": "/repo/project/.worktrees/a",
        "zellij_session": "project-a",
        "title": "Waiting for approval",
        "heartbeat_at_ms": 1777160883999,
    }))
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
        sidecar_runs_dir=sidecar_dir,
    )

    assert len(snapshot.agent_runs) == 1
    run = snapshot.agent_runs[0]
    assert run.status == AgentStatus.WAITING_APPROVAL
    assert run.workspace_group == 5
    assert run.zellij_session == "project-a"
    assert run.telemetry.title == "Waiting for approval"
    assert run.telemetry.model == "gpt-5.5"
    assert run.telemetry.tokens_used == 9000
    assert run.telemetry.heartbeat_at_ms == 1777160883999


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes")
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_stopped_sidecar_is_not_repromoted_by_process_discovery(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    sidecar_dir = tmp_path / "runs"
    sidecar_path = sidecar_dir / "project--a--main" / "status.json"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::a": {
                "branch": "a",
                "worktree_path": ".worktrees/a",
                "project_root": "/repo/project",
            }
        }
    }))
    overlay_path.write_text(json.dumps({"agent_runs": {}}))
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_text(json.dumps({
        "run_id": "project::a::main",
        "worktree_id": "project::a",
        "client": "codex",
        "status": "stopped",
        "cwd": "/repo/project/.worktrees/a",
        "heartbeat_at_ms": 1777160883999,
    }))
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
        sidecar_runs_dir=sidecar_dir,
    )

    assert len(snapshot.agent_runs) == 1
    assert snapshot.agent_runs[0].status == AgentStatus.STOPPED
    assert snapshot.agent_runs[0].zellij_session == "project-a"


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.list_zellij_sessions", return_value=["project-a"])
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_marks_overlay_run_running_when_zellij_session_exists(
    mock_node,
    mock_processes,
    mock_list_zellij_sessions,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    sidecar_dir = tmp_path / "runs"
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
                "zellij_session": "project-a",
            }
        }
    }))

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
        sidecar_runs_dir=sidecar_dir,
    )

    assert len(snapshot.agent_runs) == 1
    assert snapshot.agent_runs[0].status == AgentStatus.RUNNING


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.list_zellij_sessions", return_value=["project-a"])
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_adds_default_run_for_matching_zellij_session(
    mock_node,
    mock_processes,
    mock_list_zellij_sessions,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    sidecar_dir = tmp_path / "runs"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::a": {
                "branch": "a",
                "worktree_path": ".worktrees/a",
                "project_root": "/repo/project",
            }
        }
    }))
    overlay_path.write_text(json.dumps({"agent_runs": {}}))

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
        sidecar_runs_dir=sidecar_dir,
    )

    assert len(snapshot.agent_runs) == 1
    run = snapshot.agent_runs[0]
    assert run.id == "project::a::main"
    assert run.client == ClientName.CODEX
    assert run.status == AgentStatus.RUNNING
    assert run.zellij_session == "project-a"


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.list_zellij_sessions", return_value=["project-a"])
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_does_not_repromote_stopped_sidecar_from_zellij(
    mock_node,
    mock_processes,
    mock_list_zellij_sessions,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    sidecar_dir = tmp_path / "runs"
    status_path = sidecar_dir / "project--a--main" / "status.json"
    devtools_path.write_text(json.dumps({
        "instances": {
            "project::a": {
                "branch": "a",
                "worktree_path": ".worktrees/a",
                "project_root": "/repo/project",
            }
        }
    }))
    overlay_path.write_text(json.dumps({"agent_runs": {}}))
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({
        "run_id": "project::a::main",
        "worktree_id": "project::a",
        "client": "codex",
        "status": "stopped",
        "zellij_session": "project-a",
        "heartbeat_at_ms": 1777160883999,
    }))

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
        sidecar_runs_dir=sidecar_dir,
    )

    assert len(snapshot.agent_runs) == 1
    assert snapshot.agent_runs[0].status == AgentStatus.STOPPED


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_adds_sidecar_only_run(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    sidecar_dir = tmp_path / "runs"
    status_path = sidecar_dir / "project--a--review" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({
        "run_id": "project::a::review",
        "worktree_id": "project::a",
        "client": "codex",
        "status": "idle",
        "cwd": "/repo/project/.worktrees/a",
    }))

    snapshot = build_host_snapshot(
        devtools_registry_path=tmp_path / "missing-instances.json",
        overlay_path=tmp_path / "missing-sessions.json",
        sidecar_runs_dir=sidecar_dir,
    )

    assert len(snapshot.agent_runs) == 1
    assert snapshot.agent_runs[0].id == "project::a::review"
    assert snapshot.agent_runs[0].status == AgentStatus.IDLE


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_preserves_multiple_codex_sidecars_in_same_worktree(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    devtools_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    sidecar_dir = tmp_path / "runs"
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
                "workspace_group": 3,
            }
        }
    }))
    for run_id, thread_id, title in [
        ("project::a::main", "thread-main", "Main task"),
        ("project::a::review", "thread-review", "Review task"),
    ]:
        status_path = sidecar_dir / run_id.replace("::", "--") / "status.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text(json.dumps({
            "run_id": run_id,
            "worktree_id": "project::a",
            "client": "codex",
            "status": "idle",
            "cwd": "/repo/project/.worktrees/a",
            "thread_id": thread_id,
            "title": title,
        }))

    snapshot = build_host_snapshot(
        devtools_registry_path=devtools_path,
        overlay_path=overlay_path,
        sidecar_runs_dir=sidecar_dir,
    )

    runs = {run.id: run for run in snapshot.agent_runs}
    assert set(runs) == {"project::a::main", "project::a::review"}
    assert runs["project::a::main"].workspace_group == 3
    assert runs["project::a::main"].client_ids["codex_thread_id"] == "thread-main"
    assert runs["project::a::main"].telemetry.title == "Main task"
    assert runs["project::a::review"].client_ids["codex_thread_id"] == "thread-review"
    assert runs["project::a::review"].telemetry.title == "Review task"


@patch("agent_monitor.registry._hyprland_available", return_value=False)
@patch("agent_monitor.registry.find_codex_processes", return_value=[])
@patch("agent_monitor.registry.platform.node", return_value="test-host")
def test_build_host_snapshot_prunes_stopped_ephemeral_sidecar(
    mock_node,
    mock_processes,
    mock_hyprland,
    tmp_path,
):
    sidecar_dir = tmp_path / "runs"
    status_path = sidecar_dir / "agent-monitor--manual--main" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({
        "run_id": "agent-monitor::manual::main",
        "worktree_id": "agent-monitor::manual",
        "client": "codex",
        "status": "stopped",
        "cwd": "/repo/agent-monitor",
        "heartbeat_at_ms": 1000,
    }))

    snapshot = build_host_snapshot(
        devtools_registry_path=tmp_path / "missing-instances.json",
        overlay_path=tmp_path / "missing-sessions.json",
        sidecar_runs_dir=sidecar_dir,
    )

    assert snapshot.agent_runs == []
    assert not status_path.exists()


def test_missing_registry_files_are_empty(tmp_path):
    assert read_devtools_worktrees(tmp_path / "missing-instances.json") == []
    assert read_overlay_agent_runs(tmp_path / "missing-sessions.json") == []
