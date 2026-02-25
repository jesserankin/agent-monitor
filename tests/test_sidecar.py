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
    "cwd": "/home/user/my-project",
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

    def test_writes_json_file_by_cwd(self, tmp_path):
        monitor_dir = tmp_path / "claude-monitor"
        result = run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "my-session",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        # File should be named by CWD basename, not ZELLIJ_SESSION_NAME
        json_file = monitor_dir / "my-project.json"
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

    def test_uses_cwd_basename(self, tmp_path):
        """CWD basename should be preferred over ZELLIJ_SESSION_NAME."""
        run_sidecar(
            json.dumps(SAMPLE_INPUT),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "zellij-name",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        monitor_dir = tmp_path / "claude-monitor"
        # Should use CWD basename "my-project", not ZELLIJ_SESSION_NAME
        assert (monitor_dir / "my-project.json").exists()
        assert not (monitor_dir / "zellij-name.json").exists()

    def test_falls_back_to_session_id_when_no_cwd(self, tmp_path):
        input_no_cwd = {k: v for k, v in SAMPLE_INPUT.items() if k != "cwd"}
        env = {
            "XDG_RUNTIME_DIR": str(tmp_path),
            "ZELLIJ_SESSION_NAME": "",
        }

        run_sidecar(json.dumps(input_no_cwd), env_overrides=env)

        monitor_dir = tmp_path / "claude-monitor"
        assert (monitor_dir / "test-session.json").exists()

    def test_falls_back_to_unknown_with_pid(self, tmp_path):
        """No cwd and no session_id -> unknown-PID."""
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
        """Special characters in CWD basename should be stripped."""
        input_data = dict(SAMPLE_INPUT, cwd="/home/user/my project/../../etc")
        run_sidecar(
            json.dumps(input_data),
            env_overrides={
                "ZELLIJ_SESSION_NAME": "irrelevant",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )

        monitor_dir = tmp_path / "claude-monitor"
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
        """JSON without cost/model/context/cwd fields should still work (falls back to session_id)."""
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
        # No cwd in JSON, so falls back to session_id
        assert (monitor_dir / "minimal.json").exists()

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
