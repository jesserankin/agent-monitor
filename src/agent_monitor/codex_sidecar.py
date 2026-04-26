"""Codex sidecar wrapper command."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

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
    def write_status(status: AgentStatus, *, exit_code: int | None = None, error: str | None = None) -> None:
        payload = _status_payload(
            run_id=run_id,
            worktree_id=worktree_id,
            status=status,
            cwd=cwd,
            zellij_session=zellij_session,
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

    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                break
            sleep(heartbeat_interval)
            if process.poll() is None:
                write_status(AgentStatus.RUNNING)
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
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if error:
        payload["error"] = error
    return payload


def _worktree_id_from_run_id(run_id: str) -> str:
    if "::" not in run_id:
        return run_id
    return run_id.rsplit("::", 1)[0]
