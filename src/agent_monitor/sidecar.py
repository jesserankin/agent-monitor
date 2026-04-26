"""Generic agent-monitor sidecar status files."""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

from agent_monitor.models import AgentRun, AgentStatus, ClientName


def default_agent_monitor_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "agent-monitor"


def default_sidecar_runs_dir() -> Path:
    return default_agent_monitor_dir() / "runs"


def sidecar_status_path(
    run_id: str,
    *,
    runs_dir: str | Path | None = None,
    status_path: str | Path | None = None,
) -> Path:
    """Return the sidecar status path for a run."""
    if status_path is not None:
        return Path(status_path)
    base = Path(runs_dir) if runs_dir is not None else default_sidecar_runs_dir()
    return base / safe_run_dir_name(run_id) / "status.json"


def safe_run_dir_name(run_id: str) -> str:
    """Create a filesystem-safe directory name for a run id."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "--", run_id).strip("-")
    return name[:120] or "agent-run"


def write_sidecar_status(path: str | Path, payload: dict[str, Any]) -> None:
    """Atomically write a sidecar status JSON file."""
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp_path = status_path.with_name(f".{status_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp_path.chmod(0o600)
    os.replace(tmp_path, status_path)


def read_sidecar_agent_runs(path: str | Path | None = None) -> list[AgentRun]:
    """Read agent run status files written by wrappers or client adapters."""
    runs_dir = Path(path) if path is not None else default_sidecar_runs_dir()
    if not runs_dir.exists():
        return []

    runs: list[AgentRun] = []
    for status_path in _status_files(runs_dir):
        run = _read_sidecar_file(status_path)
        if run is not None:
            runs.append(run)
    return sorted(runs, key=lambda run: run.id)


def _status_files(runs_dir: Path) -> list[Path]:
    direct_files = [path for path in runs_dir.glob("*.json") if not path.name.startswith(".")]
    nested_files = [
        path
        for path in runs_dir.glob("*/status.json")
        if not path.name.startswith(".") and not path.parent.name.startswith(".")
    ]
    return sorted({*direct_files, *nested_files})


def _read_sidecar_file(path: Path) -> AgentRun | None:
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    run_id = _optional_str(raw.get("run_id")) or _run_id_from_path(path)
    if not run_id:
        return None

    payload: dict[str, Any] = {
        "worktree_id": raw.get("worktree_id") or _worktree_id_from_run_id(run_id),
        "client": _enum_value(ClientName, raw.get("client"), ClientName.UNKNOWN),
        "status": _enum_value(AgentStatus, raw.get("status"), AgentStatus.UNKNOWN),
        "workspace_group": raw.get("workspace_group"),
        "zellij_session": raw.get("zellij_session"),
        "agent_pane": raw.get("agent_pane"),
        "cwd": raw.get("cwd"),
        "client_ids": _client_ids(raw),
        "launch": raw.get("launch") if isinstance(raw.get("launch"), dict) else {},
        "telemetry": {
            "title": raw.get("title"),
            "model": raw.get("model"),
            "tokens_used": raw.get("tokens_used"),
            "updated_at_ms": raw.get("updated_at_ms"),
            "active_since_ms": raw.get("active_since_ms"),
            "heartbeat_at_ms": raw.get("heartbeat_at_ms"),
            "context_used_pct": raw.get("context_used_pct"),
            "cost_usd": raw.get("cost_usd"),
        },
    }
    return AgentRun.from_dict(run_id, payload)


def _run_id_from_path(path: Path) -> str | None:
    if path.name == "status.json":
        return path.parent.name
    return path.stem


def _worktree_id_from_run_id(run_id: str) -> str:
    if "::" not in run_id:
        return run_id
    return run_id.rsplit("::", 1)[0]


def _client_ids(raw: dict[str, Any]) -> dict[str, Any]:
    client_ids = raw.get("client_ids") if isinstance(raw.get("client_ids"), dict) else {}
    thread_id = raw.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return {**client_ids, "codex_thread_id": thread_id}
    return dict(client_ids)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _enum_value(enum_type: type[Enum], value: Any, default: Enum) -> str:
    try:
        return enum_type(str(value)).value
    except (TypeError, ValueError):
        return default.value
