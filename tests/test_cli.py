"""Tests for agent-monitor command helpers."""

import json
import sys

import pytest

from agent_monitor.app import main
from agent_monitor.registry import read_overlay_agent_runs


def _write_devtools_registry(path, worktree_path="/repo/project/.worktrees/branch"):
    path.write_text(json.dumps({
        "instances": {
            "project::branch": {
                "branch": "branch",
                "worktree_path": worktree_path,
                "project_root": "/repo/project",
            }
        }
    }))


def test_open_run_cli_resolves_worktree_id_to_default_codex_run(tmp_path, capsys, monkeypatch):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    _write_devtools_registry(registry_path)
    attached = []

    def fake_attach(session_name, **kwargs):
        attached.append((session_name, kwargs))
        return True

    monkeypatch.setattr("agent_monitor.hosts.attach_session", fake_attach)

    main([
        "open-run",
        "project::branch",
        "--json",
        "--devtools-registry",
        str(registry_path),
        "--overlay",
        str(overlay_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "open-run"
    assert payload["action"] == "created_session"
    assert payload["resolved_as"] == "worktree"
    assert payload["run"]["id"] == "project::branch::main"
    assert payload["run"]["client"] == "codex"
    assert payload["run"]["zellij_session"] == "project-branch"
    assert attached[0][0] == "project-branch"
    assert attached[0][1]["launch_argv"] == [
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
    ]


def test_open_run_cli_resolves_default_run_id_for_worktree_without_overlay(tmp_path, capsys, monkeypatch):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    _write_devtools_registry(registry_path)
    monkeypatch.setattr("agent_monitor.hosts.attach_session", lambda *_args, **_kwargs: True)

    main([
        "open-run",
        "project::branch::main",
        "--json",
        "--devtools-registry",
        str(registry_path),
        "--overlay",
        str(overlay_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["resolved_as"] == "default-run"
    assert payload["run"]["id"] == "project::branch::main"


def test_open_run_cli_uses_existing_default_run_for_bare_worktree_target(tmp_path, capsys, monkeypatch):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    _write_devtools_registry(registry_path)
    overlay_path.write_text(json.dumps({
        "agent_runs": {
            "project::branch::main": {
                "worktree_id": "project::branch",
                "client": "codex",
                "workspace_group": 4,
                "zellij_session": "saved-session",
                "cwd": "/repo/project/.worktrees/branch",
            }
        }
    }))
    attached = []

    def fake_attach(session_name, **kwargs):
        attached.append((session_name, kwargs))
        return True

    monkeypatch.setattr("agent_monitor.hosts.attach_session", fake_attach)

    main([
        "open-run",
        "project::branch",
        "--json",
        "--devtools-registry",
        str(registry_path),
        "--overlay",
        str(overlay_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["action"] == "opened_terminal"
    assert payload["resolved_as"] == "worktree"
    assert payload["run"]["zellij_session"] == "saved-session"
    assert attached == [
        (
            "saved-session",
            {
                "workspace_group": 4,
                "create": False,
                "cwd": "/repo/project/.worktrees/branch",
                "launch_argv": None,
                "pane_name": "agent",
            },
        )
    ]


def test_set_group_cli_resolves_worktree_id_and_persists_default_run(tmp_path, capsys):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    _write_devtools_registry(registry_path)

    main([
        "set-group",
        "project::branch",
        "7",
        "--json",
        "--devtools-registry",
        str(registry_path),
        "--overlay",
        str(overlay_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "set-group"
    assert payload["resolved_as"] == "worktree"
    assert payload["run"]["id"] == "project::branch::main"
    assert payload["run"]["workspace_group"] == 7

    persisted = read_overlay_agent_runs(overlay_path)
    assert len(persisted) == 1
    assert persisted[0].id == "project::branch::main"
    assert persisted[0].workspace_group == 7


def test_set_group_cli_returns_json_error_for_invalid_group(tmp_path, capsys):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    _write_devtools_registry(registry_path)

    with pytest.raises(SystemExit) as exc_info:
        main([
            "set-group",
            "project::branch",
            "10",
            "--json",
            "--devtools-registry",
            str(registry_path),
            "--overlay",
            str(overlay_path),
        ])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False,
        "command": "set-group",
        "target": "project::branch",
        "error": {
            "code": "invalid_group",
            "message": "workspace_group must be 1-9",
        },
    }


def test_open_run_cli_returns_json_error_for_unknown_target(tmp_path, capsys):
    registry_path = tmp_path / "instances.json"
    overlay_path = tmp_path / "sessions.json"
    _write_devtools_registry(registry_path)

    with pytest.raises(SystemExit) as exc_info:
        main([
            "open-run",
            "missing::branch",
            "--json",
            "--devtools-registry",
            str(registry_path),
            "--overlay",
            str(overlay_path),
        ])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"


def test_codex_cli_infers_worktree_and_default_run_from_cwd(tmp_path, monkeypatch):
    registry_path = tmp_path / "instances.json"
    worktree_path = tmp_path / "project" / ".worktrees" / "branch"
    worktree_path.mkdir(parents=True)
    _write_devtools_registry(registry_path, worktree_path=str(worktree_path))
    calls = []

    def fake_sidecar(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.chdir(worktree_path)
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "project-branch")
    monkeypatch.setattr("agent_monitor.app.run_codex_sidecar", fake_sidecar)

    with pytest.raises(SystemExit) as exc_info:
        main([
            "codex",
            "--devtools-registry",
            str(registry_path),
        ])

    assert exc_info.value.code == 0
    assert calls == [{
        "run_id": "project::branch::main",
        "worktree_id": "project::branch",
        "cwd": str(worktree_path),
        "zellij_session": "project-branch",
        "codex_thread_id": None,
        "runs_dir": None,
        "status_path": None,
        "heartbeat_interval": 5.0,
        "cleanup_stopped_status": False,
        "command": ["codex", "--cd", str(worktree_path)],
    }]


def test_codex_cli_supports_named_run_and_custom_args(tmp_path, monkeypatch):
    registry_path = tmp_path / "instances.json"
    worktree_path = tmp_path / "project" / ".worktrees" / "branch"
    worktree_path.mkdir(parents=True)
    _write_devtools_registry(registry_path, worktree_path=str(worktree_path))
    calls = []

    monkeypatch.chdir(worktree_path)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    monkeypatch.setattr("agent_monitor.app.run_codex_sidecar", lambda **kwargs: calls.append(kwargs) or 0)

    with pytest.raises(SystemExit):
        main([
            "codex",
            "--run-name",
            "review",
            "--codex-thread-id",
            "thread-123",
            "--devtools-registry",
            str(registry_path),
            "--",
            "--model",
            "gpt-5.5",
        ])

    assert calls[0]["run_id"] == "project::branch::review"
    assert calls[0]["worktree_id"] == "project::branch"
    assert calls[0]["zellij_session"] is None
    assert calls[0]["codex_thread_id"] == "thread-123"
    assert calls[0]["cleanup_stopped_status"] is False
    assert calls[0]["command"] == ["codex", "--model", "gpt-5.5"]


def test_codex_cli_cleans_up_non_devtools_manual_run_by_default(tmp_path, monkeypatch):
    calls = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent_monitor.app.run_codex_sidecar", lambda **kwargs: calls.append(kwargs) or 0)

    with pytest.raises(SystemExit) as exc_info:
        main([
            "codex",
            "--run-id",
            "agent-monitor",
        ])

    assert exc_info.value.code == 0
    assert calls[0]["run_id"] == "agent-monitor"
    assert calls[0]["worktree_id"] == "agent-monitor"
    assert calls[0]["cleanup_stopped_status"] is True


def test_codex_cli_keep_status_disables_ephemeral_cleanup(tmp_path, monkeypatch):
    calls = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent_monitor.app.run_codex_sidecar", lambda **kwargs: calls.append(kwargs) or 0)

    with pytest.raises(SystemExit):
        main([
            "codex",
            "--run-id",
            "agent-monitor",
            "--keep-status",
        ])

    assert calls[0]["cleanup_stopped_status"] is False


def test_codex_cli_errors_when_worktree_cannot_be_inferred(tmp_path, monkeypatch, capsys):
    registry_path = tmp_path / "instances.json"
    _write_devtools_registry(registry_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main([
            "codex",
            "--devtools-registry",
            str(registry_path),
        ])

    assert exc_info.value.code == 2
    assert "could not infer worktree id" in capsys.readouterr().err
