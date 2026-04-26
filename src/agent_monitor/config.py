"""Configuration loading for agent-monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


def default_config_path() -> Path:
    return Path.home() / ".config" / "agent-monitor" / "config.toml"


@dataclass(frozen=True)
class RemoteHostConfig:
    name: str
    host: str
    agent_monitor_command: str = "agent-monitor"


@dataclass(frozen=True)
class AgentMonitorConfig:
    remotes: list[RemoteHostConfig] = field(default_factory=list)


def read_config(path: str | Path | None = None) -> AgentMonitorConfig:
    """Read agent-monitor config.

    Missing config files and malformed remote entries are treated as empty so
    the local-only path remains the default behavior.
    """
    config_path = Path(path) if path is not None else default_config_path()
    try:
        data = tomllib.loads(config_path.read_text())
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return AgentMonitorConfig()
    if not isinstance(data, dict):
        return AgentMonitorConfig()
    return AgentMonitorConfig(remotes=_parse_remotes(data.get("remotes")))


def _parse_remotes(value: Any) -> list[RemoteHostConfig]:
    if not isinstance(value, list):
        return []
    remotes: list[RemoteHostConfig] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        host = item.get("host")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(host, str) or not host.strip():
            continue
        command = item.get("agent_monitor_command", "agent-monitor")
        if not isinstance(command, str) or not command.strip():
            command = "agent-monitor"
        remotes.append(
            RemoteHostConfig(
                name=name.strip(),
                host=host.strip(),
                agent_monitor_command=command.strip(),
            )
        )
    return remotes
