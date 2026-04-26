"""Tests for agent-monitor config parsing."""

from agent_monitor.config import read_config


def test_read_config_returns_empty_for_missing_file(tmp_path):
    config = read_config(tmp_path / "missing.toml")

    assert config.remotes == []


def test_read_config_parses_remote_hosts(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[[remotes]]
name = "workstation"
host = "jesse@workstation.local"

[[remotes]]
name = "cloud-dev"
host = "jesse@dev.example.com"
agent_monitor_command = "/home/jesse/.local/bin/agent-monitor"
"""
    )

    config = read_config(path)

    assert [remote.name for remote in config.remotes] == ["workstation", "cloud-dev"]
    assert config.remotes[0].host == "jesse@workstation.local"
    assert config.remotes[0].agent_monitor_command == "agent-monitor"
    assert config.remotes[1].agent_monitor_command == "/home/jesse/.local/bin/agent-monitor"


def test_read_config_ignores_invalid_remote_entries(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[[remotes]]
name = "missing-host"

[[remotes]]
host = "missing-name"

[[remotes]]
name = "valid"
host = "valid.example.com"
"""
    )

    config = read_config(path)

    assert [remote.name for remote in config.remotes] == ["valid"]
