"""Registry readers for dev-tools worktrees and agent-monitor session overlay."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_monitor.hyprland import get_event_socket_path
from agent_monitor.models import AgentRun, AgentStatus, ClientName, HostInfo, HostSnapshot, Worktree
from agent_monitor.procfs import find_codex_processes


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
    include_stopped_worktrees: bool = True,
    include_processes: bool = True,
) -> HostSnapshot:
    """Build a local normalized snapshot from the registries currently available."""
    worktrees = read_devtools_worktrees(devtools_registry_path)
    runs = read_overlay_agent_runs(overlay_path)

    if include_processes:
        runs = _merge_codex_processes(worktrees, runs, find_codex_processes())

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


def _merge_codex_processes(
    worktrees: list[Worktree],
    runs: list[AgentRun],
    processes: list[dict[str, Any]],
) -> list[AgentRun]:
    merged = list(runs)
    matched_worktrees: set[str] = set()
    for process in processes:
        cwd = process.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            continue
        worktree = _find_worktree_for_cwd(worktrees, cwd)
        if worktree is None or worktree.id in matched_worktrees:
            continue
        matched_worktrees.add(worktree.id)

        run = _find_codex_run_for_worktree(merged, worktree)
        if run is None:
            run = AgentRun(
                id=_codex_run_id(worktree, merged),
                worktree_id=worktree.id,
                client=ClientName.CODEX,
            )
            merged.append(run)

        run.client = ClientName.CODEX
        run.status = AgentStatus.RUNNING
        run.cwd = cwd
        zellij_session = process.get("zellij_session_name")
        if isinstance(zellij_session, str) and zellij_session:
            run.zellij_session = zellij_session

    merged.sort(key=lambda item: item.id)
    return merged


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
