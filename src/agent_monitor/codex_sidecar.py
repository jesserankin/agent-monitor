"""Codex sidecar wrapper command."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent_monitor.clients.codex import CodexTelemetry, CodexTelemetryReader
from agent_monitor.models import AgentStatus, ClientName
from agent_monitor.sidecar import sidecar_status_path, write_sidecar_status


def current_time_ms() -> int:
    return int(time.time() * 1000)


def run_codex_sidecar(
    *,
    run_id: str,
    command: Sequence[str],
    worktree_id: str | None = None,
    cwd: str | None = None,
    zellij_session: str | None = None,
    runs_dir: str | Path | None = None,
    status_path: str | Path | None = None,
    heartbeat_interval: float = 5.0,
    telemetry_reader: Callable[[], CodexTelemetry | None] | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = current_time_ms,
) -> int:
    """Run Codex while maintaining a sidecar status file."""
    if not command:
        raise ValueError("command is required")

    resolved_status_path = sidecar_status_path(
        run_id,
        runs_dir=runs_dir,
        status_path=status_path,
    )
    def write_status(
        status: AgentStatus,
        *,
        exit_code: int | None = None,
        error: str | None = None,
        telemetry: CodexTelemetry | None = None,
    ) -> None:
        payload = _status_payload(
            run_id=run_id,
            worktree_id=worktree_id,
            status=telemetry.status if telemetry and telemetry.status is not None else status,
            cwd=cwd,
            zellij_session=zellij_session,
            telemetry=telemetry,
            heartbeat_at_ms=now_ms(),
            exit_code=exit_code,
            error=error,
        )
        write_sidecar_status(resolved_status_path, payload)

    write_status(AgentStatus.RUNNING)
    try:
        process = popen_factory(list(command), cwd=cwd or None)
    except OSError as exc:
        write_status(AgentStatus.ERROR, exit_code=127, error=str(exc))
        return 127
    if telemetry_reader is None:
        reader = CodexTelemetryReader(cwd=cwd, process_pid=getattr(process, "pid", None))
        telemetry_reader = reader.read

    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                break
            sleep(heartbeat_interval)
            if process.poll() is None:
                write_status(AgentStatus.RUNNING, telemetry=_read_telemetry(telemetry_reader))
    except KeyboardInterrupt:
        process.terminate()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
        status = AgentStatus.STOPPED if return_code == 0 else AgentStatus.ERROR
        error = None if status == AgentStatus.STOPPED else "interrupted"
        write_status(status, exit_code=return_code, error=error)
        return int(return_code) if return_code is not None else 130

    status = AgentStatus.STOPPED if return_code == 0 else AgentStatus.ERROR
    write_status(status, exit_code=return_code)
    return int(return_code)


def _status_payload(
    *,
    run_id: str,
    worktree_id: str | None,
    status: AgentStatus,
    cwd: str | None,
    zellij_session: str | None,
    telemetry: CodexTelemetry | None,
    heartbeat_at_ms: int,
    exit_code: int | None,
    error: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "run_id": run_id,
        "worktree_id": worktree_id or _worktree_id_from_run_id(run_id),
        "client": ClientName.CODEX.value,
        "status": status.value,
        "heartbeat_at_ms": heartbeat_at_ms,
    }
    if cwd:
        payload["cwd"] = cwd
    if zellij_session:
        payload["zellij_session"] = zellij_session
    if telemetry:
        if telemetry.thread_id:
            payload["thread_id"] = telemetry.thread_id
        if telemetry.title:
            payload["title"] = telemetry.title
        if telemetry.model:
            payload["model"] = telemetry.model
        if telemetry.tokens_used is not None:
            payload["tokens_used"] = telemetry.tokens_used
        if telemetry.updated_at_ms is not None:
            payload["updated_at_ms"] = telemetry.updated_at_ms
        if status == AgentStatus.ACTIVE and telemetry.active_since_ms is not None:
            payload["active_since_ms"] = telemetry.active_since_ms
        if telemetry.context_used_pct is not None:
            payload["context_used_pct"] = telemetry.context_used_pct
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if error:
        payload["error"] = error
    return payload


def _read_telemetry(reader: Callable[[], CodexTelemetry | None]) -> CodexTelemetry | None:
    try:
        return reader()
    except Exception:
        return None


def _worktree_id_from_run_id(run_id: str) -> str:
    if "::" not in run_id:
        return run_id
    return run_id.rsplit("::", 1)[0]
