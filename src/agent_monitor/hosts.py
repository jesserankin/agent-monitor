"""Host adapters for normalized agent-monitor snapshots."""

from __future__ import annotations

import sys
import os
import re
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from agent_monitor.config import AgentMonitorConfig, RemoteHostConfig, read_config
from agent_monitor.models import AgentRun, ClientName, HostInfo, HostSnapshot, Worktree
from agent_monitor.hyprland import find_zellij_window_sync
from agent_monitor.registry import (
    build_host_snapshot,
    read_devtools_worktrees,
    set_overlay_workspace_group,
    set_overlay_zellij_session,
)
from agent_monitor.ssh import SshCommandError, SshTransport, open_ssh_zellij_attach
from agent_monitor.workspace import focus_window_sync, move_window_to_workspace, switch_to_group_sync
from agent_monitor.zellij import attach_session, ensure_session, list_sessions, middle_workspace_for_group, session_name_for_run_id


class HostAdapter(Protocol):
    """A source of normalized host state."""

    def snapshot(self) -> HostSnapshot:
        """Return the latest host snapshot."""

    def set_workspace_group(self, run: AgentRun, workspace_group: int) -> AgentRun:
        """Persist a run workspace group."""

    def open_run(self, run: AgentRun) -> bool:
        """Open or attach to an existing run."""


class LocalHostAdapter:
    """Local filesystem/subprocess-backed host adapter."""

    def __init__(
        self,
        *,
        host_name: str | None = None,
        devtools_registry_path: str | Path | None = None,
        overlay_path: str | Path | None = None,
        sidecar_runs_dir: str | Path | None = None,
    ) -> None:
        self.host_name = host_name
        self.devtools_registry_path = devtools_registry_path
        self.overlay_path = overlay_path
        self.sidecar_runs_dir = sidecar_runs_dir
        self.last_open_action: str | None = None

    def snapshot(self) -> HostSnapshot:
        return build_host_snapshot(
            host_name=self.host_name,
            devtools_registry_path=self.devtools_registry_path,
            overlay_path=self.overlay_path,
            sidecar_runs_dir=self.sidecar_runs_dir,
        )

    def set_workspace_group(self, run: AgentRun, workspace_group: int) -> AgentRun:
        updated_run = set_overlay_workspace_group(
            run,
            workspace_group,
            self.overlay_path,
        )
        _move_existing_run_window(updated_run)
        return updated_run

    def open_run(self, run: AgentRun) -> bool:
        self.last_open_action = None
        create = False
        launch_argv: Sequence[str] | None = None
        worktree = self._worktree_for_run(run)
        if not run.zellij_session:
            run = set_overlay_zellij_session(
                run,
                session_name_for_run_id(run.id),
                self.overlay_path,
            )
            create = True
            launch_argv = _launch_argv_for_run(run, worktree)
            if launch_argv and worktree and worktree.containerized:
                if not _ensure_devcontainer_running(worktree):
                    return False
        elif _focus_existing_run_window(run):
            self.last_open_action = "focused_existing_window"
            return True
        opened = attach_session(
            run.zellij_session,
            workspace_group=run.workspace_group,
            create=create,
            cwd=run.cwd,
            launch_argv=launch_argv,
            pane_name=run.agent_pane or "agent",
        )
        if opened:
            self.last_open_action = "created_session" if create else "opened_terminal"
        return opened

    def ensure_run_session(self, run: AgentRun) -> AgentRun | None:
        """Ensure the run's zellij session exists without opening a terminal."""
        self.last_open_action = None
        worktree = self._worktree_for_run(run)
        if not run.zellij_session:
            run = set_overlay_zellij_session(
                run,
                session_name_for_run_id(run.id),
                self.overlay_path,
            )

        if not run.zellij_session:
            return None

        create = run.zellij_session not in set(list_sessions())
        launch_argv = _launch_argv_for_run(run, worktree) if create else None
        if launch_argv and worktree and worktree.containerized:
            if not _ensure_devcontainer_running(worktree):
                return None

        if not ensure_session(
            run.zellij_session,
            cwd=run.cwd,
            launch_argv=launch_argv,
            pane_name=run.agent_pane or "agent",
        ):
            return None

        self.last_open_action = "created_session" if create else "existing_session"
        return run

    def _worktree_for_run(self, run: AgentRun) -> Worktree | None:
        for worktree in read_devtools_worktrees(self.devtools_registry_path):
            if worktree.id == run.worktree_id:
                return worktree
        return None


class SshHostAdapter:
    """SSH-backed host adapter that delegates state to a remote helper."""

    def __init__(
        self,
        remote: RemoteHostConfig,
        *,
        transport: SshTransport | None = None,
    ) -> None:
        self.remote = remote
        self.transport = transport or SshTransport(
            remote.host,
            agent_monitor_command=remote.agent_monitor_command,
        )
        self.last_open_action: str | None = None

    def snapshot(self) -> HostSnapshot:
        data = self.transport.run_json(["host-snapshot", "--json"])
        snapshot = HostSnapshot.from_dict(data)
        snapshot.host = replace(
            snapshot.host,
            name=self.remote.name,
            transport="ssh",
        )
        return snapshot

    def set_workspace_group(self, run: AgentRun, workspace_group: int) -> AgentRun:
        data = self.transport.run_json(["set-group", run.id, str(workspace_group), "--json"])
        if data.get("ok") is not True:
            _raise_remote_command_error(data, "set-group")
        return _run_from_command_payload(data, fallback=run)

    def open_run(self, run: AgentRun) -> bool:
        self.last_open_action = None
        data = self.transport.run_json(["open-run", run.id, "--json", "--no-attach"])
        if data.get("ok") is not True:
            return False
        self.last_open_action = _optional_str(data.get("action"))
        opened_run = _run_from_command_payload(data, fallback=run)
        if not opened_run.zellij_session:
            return True
        if open_ssh_zellij_attach(
            self.remote.host,
            opened_run.zellij_session,
            workspace_group=opened_run.workspace_group,
        ):
            self.last_open_action = "opened_ssh_terminal"
            return True
        return False


class MultiHostAdapter:
    """Host adapter that merges local and configured remote host snapshots."""

    def __init__(self, adapters: Sequence[HostAdapter]) -> None:
        self.adapters = list(adapters)
        self.last_open_action: str | None = None
        self._run_adapters: dict[int, HostAdapter] = {}
        self._worktree_adapters: dict[int, HostAdapter] = {}
        self._fallback_run_adapters: dict[str, HostAdapter] = {}
        self._fallback_worktree_adapters: dict[str, HostAdapter] = {}

    def snapshot(self) -> HostSnapshot:
        snapshots: list[HostSnapshot] = []
        self._run_adapters.clear()
        self._worktree_adapters.clear()
        self._fallback_run_adapters.clear()
        self._fallback_worktree_adapters.clear()

        for adapter in self.adapters:
            try:
                snapshot = adapter.snapshot()
            except SshCommandError:
                continue
            snapshots.append(snapshot)
            for run in snapshot.agent_runs:
                self._run_adapters[id(run)] = adapter
                self._fallback_run_adapters.setdefault(run.id, adapter)
            for worktree in snapshot.worktrees:
                self._worktree_adapters[id(worktree)] = adapter
                self._fallback_worktree_adapters.setdefault(worktree.id, adapter)

        if not snapshots:
            return HostSnapshot(host=HostInfo(name="local"))

        host_names = [snapshot.host.name for snapshot in snapshots if snapshot.host.name]
        return HostSnapshot(
            host=HostInfo(
                name=", ".join(host_names) if host_names else "local",
                transport="mixed" if len(snapshots) > 1 else snapshots[0].host.transport,
                hyprland=snapshots[0].host.hyprland,
            ),
            worktrees=[worktree for snapshot in snapshots for worktree in snapshot.worktrees],
            agent_runs=[run for snapshot in snapshots for run in snapshot.agent_runs],
        )

    def set_workspace_group(self, run: AgentRun, workspace_group: int) -> AgentRun:
        adapter = self._adapter_for_run(run)
        return adapter.set_workspace_group(run, workspace_group)

    def open_run(self, run: AgentRun) -> bool:
        self.last_open_action = None
        adapter = self._adapter_for_run(run)
        opened = adapter.open_run(run)
        self.last_open_action = getattr(adapter, "last_open_action", None)
        return opened

    def _adapter_for_run(self, run: AgentRun) -> HostAdapter:
        adapter = self._run_adapters.get(id(run)) or self._fallback_run_adapters.get(run.id)
        if adapter is not None:
            return adapter
        adapter = self._fallback_worktree_adapters.get(run.worktree_id)
        if adapter is not None:
            return adapter
        return self.adapters[0]


def configured_host_adapter(config: AgentMonitorConfig | None = None) -> HostAdapter:
    """Build the default local-plus-remote adapter from config."""
    config = config or read_config()
    local = LocalHostAdapter()
    if not config.remotes:
        return local
    return MultiHostAdapter(
        [
            local,
            *[SshHostAdapter(remote) for remote in config.remotes],
        ]
    )


def _focus_existing_run_window(run: AgentRun) -> bool:
    if not run.zellij_session:
        return False
    window = find_zellij_window_sync(run.zellij_session)
    if window is None:
        return False
    if run.workspace_group is not None:
        switch_to_group_sync(run.workspace_group)
    _move_window_for_run(run, window)
    address = window.get("address")
    if isinstance(address, str) and address:
        focus_window_sync(address)
    return True


def _move_existing_run_window(run: AgentRun) -> bool:
    if not run.zellij_session:
        return False
    window = find_zellij_window_sync(run.zellij_session)
    if window is None:
        return False
    return _move_window_for_run(run, window)


def _move_window_for_run(run: AgentRun, window: dict) -> bool:
    if run.workspace_group is None:
        return False
    address = window.get("address")
    if not isinstance(address, str) or not address:
        return False
    return move_window_to_workspace(address, middle_workspace_for_group(run.workspace_group))


def _run_from_command_payload(data: dict, *, fallback: AgentRun) -> AgentRun:
    run_data = data.get("run")
    if isinstance(run_data, dict) and run_data.get("id"):
        return AgentRun.from_dict(str(run_data["id"]), run_data)
    return fallback


def _raise_remote_command_error(data: dict, command: str) -> None:
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            raise ValueError(message)
    raise ValueError(f"remote {command} failed")


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _launch_argv_for_run(run: AgentRun, worktree: Worktree | None = None) -> Sequence[str] | None:
    argv = run.launch.get("argv")
    if isinstance(argv, list) and all(isinstance(part, str) and part for part in argv):
        if run.client == ClientName.CODEX:
            return _codex_sidecar_argv(run, argv)
        return argv
    if run.client == ClientName.CODEX and run.cwd:
        if worktree and worktree.containerized:
            return _codex_sidecar_argv(run, _devcontainer_codex_argv(worktree, run.cwd))
        return _codex_sidecar_argv(run, ["codex", "--cd", run.cwd])
    return None


def _codex_sidecar_argv(run: AgentRun, codex_argv: Sequence[str]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "agent_monitor",
        "codex-sidecar",
        "--run-id",
        run.id,
        "--worktree-id",
        run.worktree_id,
    ]
    if run.cwd:
        command.extend(["--cwd", run.cwd])
    if run.zellij_session:
        command.extend(["--zellij-session", run.zellij_session])
    thread_id = run.client_ids.get("codex_thread_id")
    if isinstance(thread_id, str) and thread_id:
        command.extend(["--codex-thread-id", thread_id])
    command.append("--")
    command.extend(codex_argv)
    return command


def _devcontainer_codex_argv(worktree: Worktree, cwd: str) -> list[str]:
    if not worktree.project_root:
        return ["codex", "--cd", cwd]
    container_cwd = _container_path_for(worktree, cwd)
    shell_command = " ".join([
        "cd",
        shlex.quote(container_cwd),
        "&&",
        "exec",
        "codex",
        "--cd",
        shlex.quote(container_cwd),
    ])
    return [
        "devcontainer",
        "exec",
        "--workspace-folder",
        worktree.project_root,
        "sh",
        "-lc",
        shell_command,
    ]


def _ensure_devcontainer_running(worktree: Worktree) -> bool:
    if not worktree.project_root:
        return False
    try:
        subprocess.run(
            ["devcontainer", "up", "--workspace-folder", worktree.project_root],
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def _container_path_for(worktree: Worktree, host_path: str) -> str:
    project_root = os.path.realpath(os.path.expanduser(worktree.project_root or ""))
    host_path = os.path.realpath(os.path.expanduser(host_path))
    workspace_folder = _workspace_folder(worktree.project_root)
    if host_path == project_root:
        return workspace_folder
    if host_path.startswith(project_root.rstrip(os.sep) + os.sep):
        return os.path.join(workspace_folder, os.path.relpath(host_path, project_root))
    return host_path


def _workspace_folder(project_root: str | None) -> str:
    if not project_root:
        return "/workspace"
    try:
        content = Path(project_root, ".devcontainer", "devcontainer.json").read_text()
    except OSError:
        return "/workspace"
    match = re.search(r'"workspaceFolder"\s*:\s*"([^"]+)"', content)
    if match:
        return match.group(1)
    return "/workspace"
