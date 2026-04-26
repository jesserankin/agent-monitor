"""Registry readers for dev-tools worktrees and agent-monitor session overlay."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_monitor.hyprland import get_event_socket_path
from agent_monitor.models import AgentRun, AgentStatus, ClientName, ClientTelemetry, HostInfo, HostSnapshot, Worktree
from agent_monitor.procfs import find_codex_processes
from agent_monitor.sidecar import prune_ephemeral_sidecar_statuses, read_sidecar_agent_runs
from agent_monitor.zellij import list_sessions as list_zellij_sessions, session_name_for_run_id


def default_devtools_registry_path() -> Path:
    return Path.home() / ".config" / "dev_tools" / "instances.json"


def default_overlay_path() -> Path:
    return Path.home() / ".config" / "agent-monitor" / "sessions.json"


def read_devtools_worktrees(path: str | Path | None = None) -> list[Worktree]:
    """Read the dev-tools worktree registry.

    Missing files return an empty list so agent-monitor can run before
    dev-tools has created any worktrees.
    """
    registry_path = Path(path) if path is not None else default_devtools_registry_path()
    data = _read_json_object(registry_path)
    instances = data.get("instances")
    if not isinstance(instances, dict):
        return []

    worktrees: list[Worktree] = []
    for worktree_id, instance in instances.items():
        if not isinstance(instance, dict):
            continue
        worktrees.append(Worktree.from_devtools_instance(str(worktree_id), instance))
    return sorted(worktrees, key=lambda item: (item.project, item.branch, item.id))


def read_overlay_agent_runs(path: str | Path | None = None) -> list[AgentRun]:
    """Read agent-monitor's overlay registry of agent runs."""
    overlay_path = Path(path) if path is not None else default_overlay_path()
    data = _read_json_object(overlay_path)
    agent_runs = data.get("agent_runs")
    if not isinstance(agent_runs, dict):
        return []

    runs: list[AgentRun] = []
    for run_id, run_data in agent_runs.items():
        if not isinstance(run_data, dict):
            continue
        runs.append(AgentRun.from_dict(str(run_id), run_data))
    return sorted(runs, key=lambda item: item.id)


def write_overlay_agent_runs(
    runs: list[AgentRun],
    path: str | Path | None = None,
) -> None:
    """Write agent-monitor's overlay atomically."""
    overlay_path = Path(path) if path is not None else default_overlay_path()
    overlay_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)

    payload = {
        "agent_runs": {
            run.id: _overlay_run_payload(run)
            for run in sorted(runs, key=lambda item: item.id)
        }
    }
    tmp_path = overlay_path.with_name(f".{overlay_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp_path, overlay_path)


def set_overlay_workspace_group(
    run: AgentRun,
    workspace_group: int,
    path: str | Path | None = None,
) -> AgentRun:
    """Upsert an overlay run with a persisted workspace group."""
    if workspace_group < 1 or workspace_group > 9:
        raise ValueError("workspace_group must be 1-9")

    runs = read_overlay_agent_runs(path)
    updated_run = replace(run, workspace_group=workspace_group)
    for index, existing in enumerate(runs):
        if existing.id == run.id:
            runs[index] = replace(
                existing,
                client=run.client,
                workspace_group=workspace_group,
                zellij_session=run.zellij_session or existing.zellij_session,
                agent_pane=run.agent_pane or existing.agent_pane,
                cwd=run.cwd or existing.cwd,
            )
            updated_run = runs[index]
            break
    else:
        runs.append(updated_run)

    write_overlay_agent_runs(runs, path)
    return updated_run


def set_overlay_zellij_session(
    run: AgentRun,
    zellij_session: str,
    path: str | Path | None = None,
) -> AgentRun:
    """Upsert an overlay run with a persisted zellij session."""
    if not zellij_session:
        raise ValueError("zellij_session is required")

    runs = read_overlay_agent_runs(path)
    updated_run = replace(run, zellij_session=zellij_session)
    for index, existing in enumerate(runs):
        if existing.id == run.id:
            runs[index] = replace(
                existing,
                client=run.client,
                workspace_group=run.workspace_group if run.workspace_group is not None else existing.workspace_group,
                zellij_session=zellij_session,
                agent_pane=run.agent_pane or existing.agent_pane,
                cwd=run.cwd or existing.cwd,
            )
            updated_run = runs[index]
            break
    else:
        runs.append(updated_run)

    write_overlay_agent_runs(runs, path)
    return updated_run


def build_host_snapshot(
    *,
    host_name: str | None = None,
    devtools_registry_path: str | Path | None = None,
    overlay_path: str | Path | None = None,
    sidecar_runs_dir: str | Path | None = None,
    include_stopped_worktrees: bool = False,
    include_sidecars: bool = True,
    include_zellij_sessions: bool = True,
    include_processes: bool = True,
) -> HostSnapshot:
    """Build a local normalized snapshot from the registries currently available."""
    worktrees = read_devtools_worktrees(devtools_registry_path)
    runs = read_overlay_agent_runs(overlay_path)
    zellij_sessions: list[str] = []
    if include_zellij_sessions:
        zellij_sessions = list_zellij_sessions()

    if include_sidecars:
        prune_ephemeral_sidecar_statuses(
            sidecar_runs_dir,
            worktree_ids={worktree.id for worktree in worktrees},
            overlay_run_ids={run.id for run in runs},
            now_ms=int(time.time() * 1000),
        )
        runs = _merge_sidecar_runs(
            worktrees,
            runs,
            read_sidecar_agent_runs(sidecar_runs_dir),
            active_zellij_sessions=set(zellij_sessions),
        )

    if include_zellij_sessions:
        runs = _merge_zellij_sessions(worktrees, runs, zellij_sessions)
        runs = _clear_invalid_live_zellij_sessions(runs, set(zellij_sessions))

    if include_processes:
        runs = _merge_codex_processes(
            worktrees,
            runs,
            find_codex_processes(),
            active_zellij_sessions=set(zellij_sessions),
        )

    if include_stopped_worktrees:
        worktree_ids_with_runs = {run.worktree_id for run in runs}
        runs.extend(
            AgentRun.stopped_for_worktree(worktree)
            for worktree in worktrees
            if worktree.id not in worktree_ids_with_runs
        )
        runs.sort(key=lambda item: item.id)

    return HostSnapshot(
        host=HostInfo(
            name=host_name or platform.node() or "local",
            transport="local",
            hyprland=_hyprland_available(),
        ),
        worktrees=worktrees,
        agent_runs=runs,
    )


def _merge_sidecar_runs(
    worktrees: list[Worktree],
    runs: list[AgentRun],
    sidecar_runs: list[AgentRun],
    *,
    active_zellij_sessions: set[str] | None = None,
) -> list[AgentRun]:
    active_zellij_sessions = active_zellij_sessions or set()
    merged = list(runs)
    for sidecar_run in sidecar_runs:
        sidecar_zellij_session = _live_zellij_session_or_none(
            sidecar_run.zellij_session,
            active_zellij_sessions,
        )
        run = _find_run_for_sidecar(merged, worktrees, sidecar_run)
        if run is None:
            sidecar_run.zellij_session = sidecar_zellij_session
            merged.append(sidecar_run)
            continue

        run.client = sidecar_run.client
        run.status = sidecar_run.status
        run.workspace_group = sidecar_run.workspace_group if sidecar_run.workspace_group is not None else run.workspace_group
        run.zellij_session = sidecar_zellij_session or run.zellij_session
        run.agent_pane = sidecar_run.agent_pane or run.agent_pane
        run.cwd = sidecar_run.cwd or run.cwd
        run.client_ids = {**run.client_ids, **sidecar_run.client_ids}
        run.launch = sidecar_run.launch or run.launch
        run.telemetry = _merge_telemetry(run.telemetry, sidecar_run.telemetry)

    merged.sort(key=lambda item: item.id)
    return merged


def _clear_invalid_live_zellij_sessions(
    runs: list[AgentRun],
    active_zellij_sessions: set[str],
) -> list[AgentRun]:
    if not active_zellij_sessions:
        return runs
    for run in runs:
        if run.status == AgentStatus.STOPPED:
            continue
        if run.zellij_session and run.zellij_session not in active_zellij_sessions:
            run.zellij_session = None
    return runs


def _live_zellij_session_or_none(
    zellij_session: str | None,
    active_zellij_sessions: set[str],
) -> str | None:
    if not zellij_session:
        return None
    if active_zellij_sessions and zellij_session not in active_zellij_sessions:
        return None
    return zellij_session


def _merge_zellij_sessions(
    worktrees: list[Worktree],
    runs: list[AgentRun],
    session_names: list[str],
) -> list[AgentRun]:
    if not session_names:
        return runs

    active_sessions = set(session_names)
    merged = list(runs)
    for run in merged:
        if run.zellij_session not in active_sessions:
            continue
        if run.status in {AgentStatus.STOPPED, AgentStatus.UNKNOWN} and not _has_sidecar_telemetry(run):
            run.status = AgentStatus.RUNNING

    run_worktree_ids = {run.worktree_id for run in merged}
    for worktree in worktrees:
        if worktree.id in run_worktree_ids:
            continue
        default_run_id = f"{worktree.id}::main"
        expected_session = session_name_for_run_id(default_run_id)
        if expected_session not in active_sessions:
            continue
        run = AgentRun.default_codex_for_worktree(worktree)
        run.status = AgentStatus.RUNNING
        run.zellij_session = expected_session
        merged.append(run)

    merged.sort(key=lambda item: item.id)
    return merged


def _has_sidecar_telemetry(run: AgentRun) -> bool:
    telemetry = run.telemetry
    return telemetry.heartbeat_at_ms is not None or telemetry.updated_at_ms is not None


def _find_run_for_sidecar(
    runs: list[AgentRun],
    worktrees: list[Worktree],
    sidecar_run: AgentRun,
) -> AgentRun | None:
    for run in runs:
        if run.id == sidecar_run.id:
            return run
    if _has_concrete_sidecar_identity(sidecar_run):
        return None
    if sidecar_run.cwd:
        worktree = _find_worktree_for_cwd(worktrees, sidecar_run.cwd)
        if worktree is not None:
            for run in runs:
                if run.worktree_id == worktree.id and run.client in {sidecar_run.client, ClientName.UNKNOWN}:
                    return run
    return None


def _has_concrete_sidecar_identity(run: AgentRun) -> bool:
    """Whether a sidecar describes a specific run rather than a legacy cwd signal."""
    if run.client_ids:
        return True
    if run.id == f"{run.worktree_id}::main":
        return True
    return run.id.startswith(f"{run.worktree_id}::")


def _merge_telemetry(existing: ClientTelemetry, incoming: ClientTelemetry) -> ClientTelemetry:
    return ClientTelemetry(
        title=incoming.title or existing.title,
        model=incoming.model or existing.model,
        tokens_used=incoming.tokens_used if incoming.tokens_used is not None else existing.tokens_used,
        updated_at_ms=incoming.updated_at_ms if incoming.updated_at_ms is not None else existing.updated_at_ms,
        active_since_ms=incoming.active_since_ms if incoming.active_since_ms is not None else existing.active_since_ms,
        heartbeat_at_ms=incoming.heartbeat_at_ms if incoming.heartbeat_at_ms is not None else existing.heartbeat_at_ms,
        context_used_pct=incoming.context_used_pct if incoming.context_used_pct is not None else existing.context_used_pct,
        cost_usd=incoming.cost_usd if incoming.cost_usd is not None else existing.cost_usd,
    )


def _merge_codex_processes(
    worktrees: list[Worktree],
    runs: list[AgentRun],
    processes: list[dict[str, Any]],
    *,
    active_zellij_sessions: set[str] | None = None,
) -> list[AgentRun]:
    merged = list(runs)
    active_zellij_sessions = active_zellij_sessions or set()
    for worktree, process in _best_codex_processes_by_worktree(worktrees, processes, active_zellij_sessions):
        cwd = process.get("cwd")
        run = _find_codex_run_for_worktree(merged, worktree)
        if run is None:
            run = AgentRun(
                id=_codex_run_id(worktree, merged),
                worktree_id=worktree.id,
                client=ClientName.CODEX,
            )
            merged.append(run)

        run.client = ClientName.CODEX
        if run.status in {AgentStatus.STOPPED, AgentStatus.UNKNOWN} and run.telemetry.heartbeat_at_ms is None:
            run.status = AgentStatus.RUNNING
        run.cwd = cwd
        zellij_session = process.get("zellij_session_name")
        if isinstance(zellij_session, str) and zellij_session:
            run.zellij_session = zellij_session

    merged.sort(key=lambda item: item.id)
    return merged


def _best_codex_processes_by_worktree(
    worktrees: list[Worktree],
    processes: list[dict[str, Any]],
    active_zellij_sessions: set[str],
) -> list[tuple[Worktree, dict[str, Any]]]:
    best: dict[str, tuple[Worktree, dict[str, Any], tuple[int, int]]] = {}
    for index, process in enumerate(processes):
        cwd = process.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            continue
        worktree = _find_worktree_for_cwd(worktrees, cwd)
        if worktree is None:
            continue

        zellij_session = process.get("zellij_session_name")
        score = 0
        if isinstance(zellij_session, str) and zellij_session:
            score = 2 if zellij_session in active_zellij_sessions else 1

        existing = best.get(worktree.id)
        rank = (score, -index)
        if existing is None or rank > existing[2]:
            best[worktree.id] = (worktree, process, rank)

    return [(worktree, process) for worktree, process, _rank in best.values()]


def _find_codex_run_for_worktree(runs: list[AgentRun], worktree: Worktree) -> AgentRun | None:
    for run in runs:
        if run.worktree_id == worktree.id and run.client == ClientName.CODEX:
            return run
    for run in runs:
        if run.worktree_id == worktree.id and run.client == ClientName.UNKNOWN:
            return run
    return None


def _codex_run_id(worktree: Worktree, runs: list[AgentRun]) -> str:
    main_id = f"{worktree.id}::main"
    if all(run.id != main_id for run in runs):
        return main_id
    return f"{worktree.id}::codex"


def _find_worktree_for_cwd(worktrees: list[Worktree], cwd: str) -> Worktree | None:
    cwd_path = _normalize_path(cwd)
    matches = [
        worktree
        for worktree in worktrees
        if worktree.path and _path_is_inside(cwd_path, _normalize_path(worktree.path))
    ]
    if not matches:
        return None
    return max(matches, key=lambda worktree: len(_normalize_path(worktree.path)))


def _path_is_inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _normalize_path(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _overlay_run_payload(run: AgentRun) -> dict[str, Any]:
    data = run.to_dict()
    data.pop("id", None)
    data.pop("status", None)
    telemetry = data.get("telemetry")
    if telemetry == {}:
        data.pop("telemetry")
    return data


def _hyprland_available() -> bool:
    try:
        get_event_socket_path()
    except FileNotFoundError:
        return False
    return True
