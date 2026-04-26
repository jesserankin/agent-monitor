"""Tests for the Codex sidecar wrapper."""

import json

from agent_monitor.codex_sidecar import run_codex_sidecar
from agent_monitor.clients.codex import CodexTelemetry
from agent_monitor.models import AgentStatus


class FakeProcess:
    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self.poll_count = 0
        self.terminated = False
        self.killed = False

    def poll(self):
        self.poll_count += 1
        if self.poll_count < 3:
            return None
        return self.return_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.return_code


class InterruptingProcess:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_run_codex_sidecar_writes_running_heartbeat_and_stopped(tmp_path):
    status_path = tmp_path / "status.json"
    times = iter([1000, 1100, 1200])
    popen_calls = []

    def popen_factory(command, cwd=None):
        popen_calls.append((command, cwd))
        return FakeProcess(return_code=0)

    return_code = run_codex_sidecar(
        run_id="project::branch::main",
        worktree_id="project::branch",
        cwd="/repo/project/.worktrees/branch",
        zellij_session="project-branch",
        status_path=status_path,
        heartbeat_interval=0,
        command=["codex", "--cd", "/repo/project/.worktrees/branch"],
        popen_factory=popen_factory,
        sleep=lambda _seconds: None,
        now_ms=lambda: next(times),
    )

    assert return_code == 0
    assert popen_calls == [(["codex", "--cd", "/repo/project/.worktrees/branch"], "/repo/project/.worktrees/branch")]
    payload = json.loads(status_path.read_text())
    assert payload["run_id"] == "project::branch::main"
    assert payload["worktree_id"] == "project::branch"
    assert payload["client"] == "codex"
    assert payload["status"] == AgentStatus.STOPPED.value
    assert payload["cwd"] == "/repo/project/.worktrees/branch"
    assert payload["zellij_session"] == "project-branch"
    assert payload["heartbeat_at_ms"] == 1200
    assert payload["exit_code"] == 0
    assert "active_since_ms" not in payload


def test_run_codex_sidecar_running_status_does_not_include_active_since(tmp_path):
    status_path = tmp_path / "status.json"
    process = FakeProcess(return_code=0)
    process.poll_count = -100
    calls = 0

    def sleep(_seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = json.loads(status_path.read_text())
            assert payload["status"] == AgentStatus.RUNNING.value
            assert "active_since_ms" not in payload
        raise KeyboardInterrupt()

    run_codex_sidecar(
        run_id="project::branch::main",
        status_path=status_path,
        heartbeat_interval=0,
        command=["codex"],
        popen_factory=lambda *_args, **_kwargs: process,
        sleep=sleep,
        now_ms=lambda: 1000,
    )


def test_run_codex_sidecar_writes_error_when_process_fails(tmp_path):
    status_path = tmp_path / "status.json"

    return_code = run_codex_sidecar(
        run_id="project::branch::main",
        status_path=status_path,
        heartbeat_interval=0,
        command=["codex"],
        popen_factory=lambda *_args, **_kwargs: FakeProcess(return_code=2),
        sleep=lambda _seconds: None,
        now_ms=lambda: 1000,
    )

    payload = json.loads(status_path.read_text())
    assert return_code == 2
    assert payload["status"] == AgentStatus.ERROR.value
    assert payload["exit_code"] == 2


def test_run_codex_sidecar_writes_error_when_launch_fails(tmp_path):
    status_path = tmp_path / "status.json"

    def popen_factory(*_args, **_kwargs):
        raise OSError("missing executable")

    return_code = run_codex_sidecar(
        run_id="project::branch::main",
        status_path=status_path,
        command=["missing-codex"],
        popen_factory=popen_factory,
        now_ms=lambda: 1000,
    )

    payload = json.loads(status_path.read_text())
    assert return_code == 127
    assert payload["status"] == AgentStatus.ERROR.value
    assert payload["exit_code"] == 127
    assert payload["error"] == "missing executable"


def test_run_codex_sidecar_treats_clean_interrupt_as_stopped(tmp_path):
    status_path = tmp_path / "status.json"

    return_code = run_codex_sidecar(
        run_id="project::branch::main",
        status_path=status_path,
        heartbeat_interval=0,
        command=["codex"],
        popen_factory=lambda *_args, **_kwargs: InterruptingProcess(),
        sleep=lambda _seconds: (_ for _item in ()).throw(KeyboardInterrupt()),
        now_ms=lambda: 1000,
    )

    payload = json.loads(status_path.read_text())
    assert return_code == 0
    assert payload["status"] == AgentStatus.STOPPED.value
    assert payload["exit_code"] == 0
    assert "error" not in payload


def test_run_codex_sidecar_writes_rich_telemetry_when_available(tmp_path):
    status_path = tmp_path / "status.json"
    process = FakeProcess(return_code=0)
    telemetry_calls = 0

    def telemetry_reader():
        nonlocal telemetry_calls
        telemetry_calls += 1
        return CodexTelemetry(
            status=AgentStatus.ACTIVE,
            thread_id="thread-1",
            title="Task title",
            model="gpt-5.5",
            tokens_used=123,
            updated_at_ms=2000,
            active_since_ms=1500,
            context_used_pct=25.0,
        )

    run_codex_sidecar(
        run_id="project::branch::main",
        status_path=status_path,
        heartbeat_interval=0,
        command=["codex"],
        telemetry_reader=telemetry_reader,
        popen_factory=lambda *_args, **_kwargs: process,
        sleep=lambda _seconds: None,
        now_ms=lambda: 3000,
    )

    assert telemetry_calls == 1
    payload = json.loads(status_path.read_text())
    assert payload["status"] == AgentStatus.STOPPED.value
    assert payload["exit_code"] == 0


def test_run_codex_sidecar_active_heartbeat_includes_active_since(tmp_path):
    status_path = tmp_path / "status.json"
    process = FakeProcess(return_code=0)
    process.poll_count = -100

    def sleep(_seconds):
        payload = json.loads(status_path.read_text())
        if payload["status"] == AgentStatus.ACTIVE.value:
            assert payload["active_since_ms"] == 1500
            assert payload["thread_id"] == "thread-1"
            raise KeyboardInterrupt()

    run_codex_sidecar(
        run_id="project::branch::main",
        status_path=status_path,
        heartbeat_interval=0,
        command=["codex"],
        telemetry_reader=lambda: CodexTelemetry(
            status=AgentStatus.ACTIVE,
            thread_id="thread-1",
            active_since_ms=1500,
        ),
        popen_factory=lambda *_args, **_kwargs: process,
        sleep=sleep,
        now_ms=lambda: 3000,
    )
