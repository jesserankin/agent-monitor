"""Data models for agent sessions and normalized host snapshots."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

BRAILLE_SPINNER_CHARS = frozenset({"\u2802", "\u2810"})  # ⠂ ⠐
IDLE_CHAR = "\u2733"  # ✳
ATTENTION_EMOJI = "\U0001f514"  # 🔔
STATUS_CHARS = BRAILLE_SPINNER_CHARS | {IDLE_CHAR}

TERMINAL_CLASSES = frozenset(
    {"Alacritty", "com.mitchellh.ghostty", "kitty", "foot", "org.wezfurlong.wezterm"}
)

MONITOR_DIR = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
    "claude-monitor",
)


class AgentState(Enum):
    ACTIVE = "active"
    IDLE = "idle"
    ATTENTION = "attention"


class AgentStatus(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ACTIVE = "active"
    IDLE = "idle"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    ERROR = "error"
    UNKNOWN = "unknown"


class ClientName(Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    UNKNOWN = "unknown"


@dataclass
class HostInfo:
    name: str
    transport: str = "local"
    hyprland: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostInfo":
        return cls(
            name=str(data.get("name") or "local"),
            transport=str(data.get("transport") or "local"),
            hyprland=bool(data.get("hyprland", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "hyprland": self.hyprland,
        }


@dataclass
class ClientTelemetry:
    title: str | None = None
    model: str | None = None
    tokens_used: int | None = None
    updated_at_ms: int | None = None
    active_since_ms: int | None = None
    heartbeat_at_ms: int | None = None
    context_used_pct: float | None = None
    cost_usd: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ClientTelemetry":
        if not isinstance(data, dict):
            return cls()
        return cls(
            title=_optional_str(data.get("title")),
            model=_optional_str(data.get("model")),
            tokens_used=_optional_int(data.get("tokens_used")),
            updated_at_ms=_optional_int(data.get("updated_at_ms")),
            active_since_ms=_optional_int(data.get("active_since_ms")),
            heartbeat_at_ms=_optional_int(data.get("heartbeat_at_ms")),
            context_used_pct=_optional_float(data.get("context_used_pct")),
            cost_usd=_optional_float(data.get("cost_usd")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _without_none({
            "title": self.title,
            "model": self.model,
            "tokens_used": self.tokens_used,
            "updated_at_ms": self.updated_at_ms,
            "active_since_ms": self.active_since_ms,
            "heartbeat_at_ms": self.heartbeat_at_ms,
            "context_used_pct": self.context_used_pct,
            "cost_usd": self.cost_usd,
        })


@dataclass
class Worktree:
    id: str
    project: str
    branch: str
    path: str
    project_root: str | None = None
    port: int | None = None
    tidewave_port: int | None = None
    mcp_name: str | None = None
    containerized: bool = False
    created_at: str | None = None

    @classmethod
    def from_devtools_instance(cls, worktree_id: str, data: dict[str, Any]) -> "Worktree":
        project_root = _optional_str(data.get("project_root"))
        project = os.path.basename(project_root) if project_root else worktree_id.split("::", 1)[0]
        branch = str(data.get("branch") or worktree_id.rsplit("::", 1)[-1])
        path = str(data.get("worktree_path") or "")
        if project_root and path and not os.path.isabs(path):
            path = os.path.join(project_root, path)
        return cls(
            id=worktree_id,
            project=project,
            branch=branch,
            path=path,
            project_root=project_root,
            port=_optional_int(data.get("port")),
            tidewave_port=_optional_int(data.get("tidewave_port")),
            mcp_name=_optional_str(data.get("mcp_name")),
            containerized=bool(data.get("containerized", False)),
            created_at=_optional_str(data.get("created_at")),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Worktree":
        return cls(
            id=str(data["id"]),
            project=str(data.get("project") or ""),
            branch=str(data.get("branch") or ""),
            path=str(data.get("path") or ""),
            project_root=_optional_str(data.get("project_root")),
            port=_optional_int(data.get("port")),
            tidewave_port=_optional_int(data.get("tidewave_port")),
            mcp_name=_optional_str(data.get("mcp_name")),
            containerized=bool(data.get("containerized", False)),
            created_at=_optional_str(data.get("created_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _without_none({
            "id": self.id,
            "project": self.project,
            "branch": self.branch,
            "path": self.path,
            "project_root": self.project_root,
            "port": self.port,
            "tidewave_port": self.tidewave_port,
            "mcp_name": self.mcp_name,
            "containerized": self.containerized,
            "created_at": self.created_at,
        })


@dataclass
class AgentRun:
    id: str
    worktree_id: str
    client: ClientName = ClientName.UNKNOWN
    status: AgentStatus = AgentStatus.STOPPED
    workspace_group: int | None = None
    zellij_session: str | None = None
    agent_pane: str | None = None
    cwd: str | None = None
    client_ids: dict[str, Any] = field(default_factory=dict)
    launch: dict[str, Any] = field(default_factory=dict)
    telemetry: ClientTelemetry = field(default_factory=ClientTelemetry)

    @classmethod
    def from_dict(cls, run_id: str, data: dict[str, Any]) -> "AgentRun":
        telemetry = data.get("telemetry")
        if not telemetry:
            telemetry = {
                "title": data.get("title"),
                "model": data.get("model"),
                "tokens_used": data.get("tokens_used"),
                "updated_at_ms": data.get("updated_at_ms"),
                "context_used_pct": data.get("context_used_pct"),
                "cost_usd": data.get("cost_usd"),
                "heartbeat_at_ms": data.get("heartbeat_at_ms"),
            }
        return cls(
            id=run_id,
            worktree_id=str(data.get("worktree_id") or ""),
            client=_parse_enum(ClientName, data.get("client"), ClientName.UNKNOWN),
            status=_parse_enum(AgentStatus, data.get("status"), AgentStatus.STOPPED),
            workspace_group=_optional_int(data.get("workspace_group")),
            zellij_session=_optional_str(data.get("zellij_session")),
            agent_pane=_optional_str(data.get("agent_pane")),
            cwd=_optional_str(data.get("cwd")),
            client_ids=data.get("client_ids") if isinstance(data.get("client_ids"), dict) else {},
            launch=data.get("launch") if isinstance(data.get("launch"), dict) else {},
            telemetry=ClientTelemetry.from_dict(telemetry),
        )

    @classmethod
    def stopped_for_worktree(cls, worktree: Worktree) -> "AgentRun":
        return cls(
            id=f"{worktree.id}::main",
            worktree_id=worktree.id,
            status=AgentStatus.STOPPED,
            cwd=worktree.path,
        )

    @classmethod
    def default_codex_for_worktree(cls, worktree: Worktree) -> "AgentRun":
        return cls(
            id=f"{worktree.id}::main",
            worktree_id=worktree.id,
            client=ClientName.CODEX,
            status=AgentStatus.STOPPED,
            cwd=worktree.path,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _without_none({
            "id": self.id,
            "worktree_id": self.worktree_id,
            "client": self.client.value,
            "status": self.status.value,
            "workspace_group": self.workspace_group,
            "zellij_session": self.zellij_session,
            "agent_pane": self.agent_pane,
            "cwd": self.cwd,
            "client_ids": self.client_ids or None,
            "launch": self.launch or None,
        })
        telemetry = self.telemetry.to_dict()
        if telemetry:
            data["telemetry"] = telemetry
        return data


@dataclass
class HostSnapshot:
    host: HostInfo
    worktrees: list[Worktree] = field(default_factory=list)
    agent_runs: list[AgentRun] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostSnapshot":
        return cls(
            host=HostInfo.from_dict(data.get("host") if isinstance(data.get("host"), dict) else {}),
            worktrees=[
                Worktree.from_dict(item)
                for item in data.get("worktrees", [])
                if isinstance(item, dict) and "id" in item
            ],
            agent_runs=[
                AgentRun.from_dict(str(item.get("id")), item)
                for item in data.get("agent_runs", [])
                if isinstance(item, dict) and item.get("id")
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host.to_dict(),
            "worktrees": [worktree.to_dict() for worktree in self.worktrees],
            "agent_runs": [run.to_dict() for run in self.agent_runs],
        }


@dataclass
class AgentSession:
    address: str
    session_name: str
    task_description: str
    state: AgentState
    workspace_id: int
    window_class: str
    status_char: str = "\u2733"
    workspace_group: int = field(init=False)
    pid: int | None = None
    cwd: str | None = None
    active_since: float | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    context_used_pct: float | None = None
    model_name: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    is_focused: bool = False

    def __post_init__(self):
        self.workspace_group = self.workspace_id % 10


def parse_window_title(title: str, window_class: str) -> dict | None:
    """Parse a Hyprland window title into session info.

    Returns a dict with session_name, state, task_description, has_attention
    or None if the title doesn't match a Claude Code session.
    """
    if window_class not in TERMINAL_CLASSES:
        return None

    if " | " not in title:
        return None

    session_part, rest = title.split(" | ", 1)

    has_attention = session_part.startswith(ATTENTION_EMOJI)
    if has_attention:
        session_part = session_part[len(ATTENTION_EMOJI) :].lstrip()

    if not session_part:
        return None

    rest = rest.lstrip()
    if not rest:
        return None

    status_char = rest[0]
    if status_char not in STATUS_CHARS:
        return None

    task_description = rest[1:].lstrip()

    if status_char in BRAILLE_SPINNER_CHARS:
        state = AgentState.ATTENTION if has_attention else AgentState.ACTIVE
    else:
        state = AgentState.ATTENTION if has_attention else AgentState.IDLE

    return {
        "session_name": session_part,
        "state": state,
        "task_description": task_description,
        "has_attention": has_attention,
        "status_char": status_char,
    }


def _parse_enum[T: Enum](enum_type: type[T], value: Any, default: T) -> T:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _without_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}
