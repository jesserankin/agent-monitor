"""Tests for generic agent-monitor sidecar status files."""

import json

from agent_monitor.models import AgentStatus, ClientName
from agent_monitor.sidecar import (
    prune_ephemeral_sidecar_statuses,
    read_sidecar_agent_runs,
    safe_run_dir_name,
    sidecar_status_path,
    write_sidecar_status,
)


def test_safe_run_dir_name():
    assert safe_run_dir_name("project::branch::main") == "project--branch--main"


def test_write_sidecar_status_round_trip(tmp_path):
    path = sidecar_status_path("project::branch::main", runs_dir=tmp_path)
    write_sidecar_status(path, {
        "version": 1,
        "run_id": "project::branch::main",
        "worktree_id": "project::branch",
        "client": "codex",
        "status": "running",
    })

    runs = read_sidecar_agent_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0].id == "project::branch::main"
    assert runs[0].status == AgentStatus.RUNNING


def test_read_nested_sidecar_status_file(tmp_path):
    status_path = tmp_path / "project--branch--main" / "status.json"
    status_path.parent.mkdir()
    status_path.write_text(json.dumps({
        "version": 1,
        "run_id": "project::branch::main",
        "worktree_id": "project::branch",
        "client": "codex",
        "status": "waiting_approval",
        "cwd": "/repo/project/.worktrees/branch",
        "thread_id": "thread-123",
        "title": "Implement telemetry",
        "model": "gpt-5.5",
        "tokens_used": 12345,
        "updated_at_ms": 1777160883214,
        "heartbeat_at_ms": 1777160883999,
    }))

    runs = read_sidecar_agent_runs(tmp_path)

    assert len(runs) == 1
    run = runs[0]
    assert run.id == "project::branch::main"
    assert run.worktree_id == "project::branch"
    assert run.client == ClientName.CODEX
    assert run.status == AgentStatus.WAITING_APPROVAL
    assert run.cwd == "/repo/project/.worktrees/branch"
    assert run.client_ids["codex_thread_id"] == "thread-123"
    assert run.telemetry.title == "Implement telemetry"
    assert run.telemetry.model == "gpt-5.5"
    assert run.telemetry.tokens_used == 12345
    assert run.telemetry.heartbeat_at_ms == 1777160883999


def test_read_sidecar_status_defaults_invalid_status_to_unknown(tmp_path):
    path = tmp_path / "project-branch.json"
    path.write_text(json.dumps({
        "run_id": "project::branch::main",
        "client": "codex",
        "status": "not-a-real-status",
    }))

    runs = read_sidecar_agent_runs(tmp_path)

    assert runs[0].status == AgentStatus.UNKNOWN


def test_read_sidecar_status_ignores_malformed_files(tmp_path):
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / ".tmp.json").write_text(json.dumps({"run_id": "ignored"}))

    assert read_sidecar_agent_runs(tmp_path) == []


def test_prune_ephemeral_sidecar_statuses_removes_stopped_sidecar_only_run(tmp_path):
    path = sidecar_status_path("agent-monitor::manual::main", runs_dir=tmp_path)
    write_sidecar_status(path, {
        "run_id": "agent-monitor::manual::main",
        "worktree_id": "agent-monitor::manual",
        "client": "codex",
        "status": "stopped",
        "heartbeat_at_ms": 1000,
    })

    prune_ephemeral_sidecar_statuses(
        tmp_path,
        worktree_ids=set(),
        overlay_run_ids=set(),
        now_ms=2000,
    )

    assert not path.exists()


def test_prune_ephemeral_sidecar_statuses_keeps_overlay_and_worktree_runs(tmp_path):
    overlay_path = sidecar_status_path("agent-monitor::manual::main", runs_dir=tmp_path)
    worktree_path = sidecar_status_path("project::branch::main", runs_dir=tmp_path)
    write_sidecar_status(overlay_path, {
        "run_id": "agent-monitor::manual::main",
        "worktree_id": "agent-monitor::manual",
        "client": "codex",
        "status": "stopped",
    })
    write_sidecar_status(worktree_path, {
        "run_id": "project::branch::main",
        "worktree_id": "project::branch",
        "client": "codex",
        "status": "stopped",
    })

    prune_ephemeral_sidecar_statuses(
        tmp_path,
        worktree_ids={"project::branch"},
        overlay_run_ids={"agent-monitor::manual::main"},
        now_ms=2000,
    )

    assert overlay_path.exists()
    assert worktree_path.exists()


def test_prune_ephemeral_sidecar_statuses_expires_old_errors(tmp_path):
    old_error = sidecar_status_path("old-error", runs_dir=tmp_path)
    new_error = sidecar_status_path("new-error", runs_dir=tmp_path)
    write_sidecar_status(old_error, {
        "run_id": "old-error",
        "client": "codex",
        "status": "error",
        "heartbeat_at_ms": 1000,
    })
    write_sidecar_status(new_error, {
        "run_id": "new-error",
        "client": "codex",
        "status": "error",
        "heartbeat_at_ms": 1900,
    })

    prune_ephemeral_sidecar_statuses(
        tmp_path,
        worktree_ids=set(),
        overlay_run_ids=set(),
        now_ms=2000,
        error_ttl_ms=500,
    )

    assert not old_error.exists()
    assert new_error.exists()
