"""Smoke tests for the statusline sidecar bash script."""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "statusline-sidecar.sh"

SAMPLE_INPUT = {
    "session_id": "test-session",
    "model": {"display_name": "Opus"},
    "context_window": {"used_percentage": 45.2},
    "cost": {
        "total_cost_usd": 1.23,
        "total_duration_ms": 45000,
        "total_lines_added": 120,
        "total_lines_removed": 30,
    },
}


def run_sidecar(input_data: str, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Run the sidecar script with given stdin and env."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=input_data,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


class TestSidecarValidPayload:
    """Test the happy path: valid JSON input produces correct output and file."""

    def test_writes_json_file(self, tmp_path):
        monitor_dir = tmp_path / "claude-monitor"
        result = run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "my-session",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        json_file = monitor_dir / "my-session.json"
        assert json_file.exists(), f"Expected {json_file} to exist. Dir contents: {list(monitor_dir.iterdir()) if monitor_dir.exists() else 'dir missing'}"

        written = json.loads(json_file.read_text())
        assert written == SAMPLE_INPUT

    def test_stdout_summary(self, tmp_path):
        result = run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "my-session",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        assert result.returncode == 0
        stdout = result.stdout.strip()
        assert "[Opus]" in stdout
        assert "45%" in stdout
        assert "$1.23" in stdout

    def test_directory_permissions(self, tmp_path):
        run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "my-session",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        monitor_dir = tmp_path / "claude-monitor"
        mode = monitor_dir.stat().st_mode
        assert stat.S_IMODE(mode) == 0o700

    def test_no_tmp_file_left(self, tmp_path):
        """Atomic write should not leave .tmp files behind."""
        run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "my-session",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        monitor_dir = tmp_path / "claude-monitor"
        tmp_files = list(monitor_dir.glob(".*"))
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"


class TestSidecarIdentityFallback:
    """Test the identity resolution fallback chain."""

    def test_uses_zellij_session_name(self, tmp_path):
        run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "zellij-name",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        monitor_dir = tmp_path / "claude-monitor"
        assert (monitor_dir / "zellij-name.json").exists()

    def test_falls_back_to_session_id(self, tmp_path):
        env = {
            "XDG_RUNTIME_DIR": str(tmp_path),
        }
        # Remove ZELLIJ_SESSION_NAME if set
        env["ZELLIJ_SESSION_NAME"] = ""

        run_sidecar(json.dumps(SAMPLE_INPUT), env_overrides=env)

        monitor_dir = tmp_path / "claude-monitor"
        assert (monitor_dir / "test-session.json").exists()

    def test_falls_back_to_unknown_with_pid(self, tmp_path):
        """No ZELLIJ_SESSION_NAME and no session_id -> unknown-PID."""
        input_data = {"model": {"display_name": "Opus"}, "cost": {}, "context_window": {}}
        env = {
            "XDG_RUNTIME_DIR": str(tmp_path),
            "ZELLIJ_SESSION_NAME": "",
        }

        result = run_sidecar(json.dumps(input_data), env_overrides=env)
        assert result.returncode == 0

        monitor_dir = tmp_path / "claude-monitor"
        json_files = list(monitor_dir.glob("unknown-*.json"))
        assert len(json_files) == 1, f"Expected one unknown-* file, got: {list(monitor_dir.iterdir())}"

    def test_sanitizes_identifier(self, tmp_path):
        """Special characters in session name should be stripped."""
        run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "my session/../../etc",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        monitor_dir = tmp_path / "claude-monitor"
        # Should have no slashes or spaces in filename
        json_files = list(monitor_dir.glob("*.json"))
        assert len(json_files) == 1
        assert "/" not in json_files[0].name
        assert " " not in json_files[0].name


class TestSidecarEdgeCases:
    """Test error handling and edge cases."""

    def test_malformed_json_input(self, tmp_path):
        """Non-JSON input should cause script to fail (jq will error)."""
        result = run_sidecar(
            "not json at all",
            env_overrides={
                "ZELLIJ_SESSION_NAME": "bad-input",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        # Script should exit non-zero on bad input
        assert result.returncode != 0

    def test_empty_input(self, tmp_path):
        """Empty stdin should cause script to fail."""
        result = run_sidecar(
            "",
            env_overrides={
                "ZELLIJ_SESSION_NAME": "empty-input",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        assert result.returncode != 0

    def test_missing_optional_fields_in_json(self, tmp_path):
        """JSON without cost/model/context fields should still work."""
        minimal = {"session_id": "minimal"}
        result = run_sidecar(
            json.dumps(minimal),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "minimal-session",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        assert result.returncode == 0
        monitor_dir = tmp_path / "claude-monitor"
        assert (monitor_dir / "minimal-session.json").exists()

    def test_non_object_nested_fields(self, tmp_path):
        """Nested fields that are non-objects should produce graceful fallback output."""
        bad_types = {"model": "x", "cost": "x", "context_window": "x"}
        result = run_sidecar(
            json.dumps(bad_types),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "bad-types",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        stdout = result.stdout.strip()
        assert "[?]" in stdout
        assert "0%" in stdout
        assert "$0.00" in stdout
