"""Host adapters for normalized agent-monitor snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from agent_monitor.models import AgentRun, ClientName, HostSnapshot
from agent_monitor.registry import build_host_snapshot, set_overlay_workspace_group, set_overlay_zellij_session
from agent_monitor.zellij import attach_session, session_name_for_run_id


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
    ) -> None:
        self.host_name = host_name
        self.devtools_registry_path = devtools_registry_path
        self.overlay_path = overlay_path

    def snapshot(self) -> HostSnapshot:
        return build_host_snapshot(
            host_name=self.host_name,
            devtools_registry_path=self.devtools_registry_path,
            overlay_path=self.overlay_path,
        )

    def set_workspace_group(self, run: AgentRun, workspace_group: int) -> AgentRun:
        return set_overlay_workspace_group(
            run,
            workspace_group,
            self.overlay_path,
        )

    def open_run(self, run: AgentRun) -> bool:
        create = False
        launch_argv: Sequence[str] | None = None
        if not run.zellij_session:
            run = set_overlay_zellij_session(
                run,
                session_name_for_run_id(run.id),
                self.overlay_path,
            )
            create = True
            launch_argv = _launch_argv_for_run(run)
        return attach_session(
            run.zellij_session,
            workspace_group=run.workspace_group,
            create=create,
            cwd=run.cwd,
            launch_argv=launch_argv,
            pane_name=run.agent_pane or "agent",
        )


def _launch_argv_for_run(run: AgentRun) -> Sequence[str] | None:
    argv = run.launch.get("argv")
    if isinstance(argv, list) and all(isinstance(part, str) and part for part in argv):
        return argv
    if run.client == ClientName.CODEX and run.cwd:
        return ["codex", "--cd", run.cwd]
    return None
