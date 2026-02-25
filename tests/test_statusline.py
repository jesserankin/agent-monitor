"""Tests for statusline file watcher."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_monitor.statusline import StatuslineWatcher


SAMPLE_STATUSLINE = {
    "session_id": "my-session",
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


class TestReadFile:
    """Test _read_file() field extraction and error handling."""

    def test_valid_json(self, tmp_path):
        path = tmp_path / "my-session.json"
        path.write_text(json.dumps(SAMPLE_STATUSLINE))

        on_update = MagicMock()
        watcher = StatuslineWatcher(monitor_dir=str(tmp_path), on_update=on_update)
        watcher._read_file(path)

        assert "my-session" in watcher.sessions
        data = watcher.sessions["my-session"]
        assert data["cwd"] == "/home/user/my-project"
        assert data["cost_usd"] == 1.23
        assert data["duration_ms"] == 45000
        assert data["context_used_pct"] == 45.2
        assert data["model_name"] == "Opus"
        assert data["lines_added"] == 120
        assert data["lines_removed"] == 30

    def test_field_extraction_missing_fields(self, tmp_path):
        """Missing nested fields should result in None values."""
        path = tmp_path / "sparse.json"
        path.write_text(json.dumps({"session_id": "sparse"}))

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_file(path)

        assert "sparse" in watcher.sessions
        data = watcher.sessions["sparse"]
        assert data["cwd"] is None
        assert data["cost_usd"] is None
        assert data["duration_ms"] is None
        assert data["context_used_pct"] is None
        assert data["model_name"] is None
        assert data["lines_added"] is None
        assert data["lines_removed"] is None

    def test_non_dict_nested_fields(self, tmp_path):
        """Nested fields that are not dicts should not crash."""
        path = tmp_path / "bad-nested.json"
        path.write_text(json.dumps({"cost": "oops", "context_window": 42, "model": True}))

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_file(path)  # should not raise

        assert "bad-nested" in watcher.sessions
        data = watcher.sessions["bad-nested"]
        assert data["cost_usd"] is None
        assert data["model_name"] is None
        assert data["context_used_pct"] is None

    def test_valid_non_object_json(self, tmp_path):
        """Valid JSON that isn't an object should be ignored, not crash."""
        for content in ["[]", '"text"', "123", "true"]:
            path = tmp_path / "non-object.json"
            path.write_text(content)

            watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
            watcher._read_file(path)  # should not raise

            assert "non-object" not in watcher.sessions

    def test_malformed_json(self, tmp_path):
        """Malformed JSON should not crash and should not store data."""
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{")

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_file(path)

        assert "bad" not in watcher.sessions

    def test_file_not_found_race(self, tmp_path):
        """File disappearing between detection and read should not crash."""
        path = tmp_path / "gone.json"
        # Don't create the file — simulate race condition

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_file(path)  # should not raise

        assert "gone" not in watcher.sessions

    def test_callback_called_on_valid_read(self, tmp_path):
        path = tmp_path / "cb-test.json"
        path.write_text(json.dumps(SAMPLE_STATUSLINE))

        on_update = MagicMock()
        watcher = StatuslineWatcher(monitor_dir=str(tmp_path), on_update=on_update)
        watcher._read_file(path)

        on_update.assert_called_once()
        name, data = on_update.call_args[0]
        assert name == "cb-test"
        assert data["cost_usd"] == 1.23

    def test_callback_not_called_on_malformed_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("invalid")

        on_update = MagicMock()
        watcher = StatuslineWatcher(monitor_dir=str(tmp_path), on_update=on_update)
        watcher._read_file(path)

        on_update.assert_not_called()

    def test_callback_not_called_on_missing_file(self, tmp_path):
        path = tmp_path / "gone.json"

        on_update = MagicMock()
        watcher = StatuslineWatcher(monitor_dir=str(tmp_path), on_update=on_update)
        watcher._read_file(path)

        on_update.assert_not_called()


class TestReadExistingFiles:
    """Test that existing files are read on startup."""

    def test_reads_existing_json_files(self, tmp_path):
        (tmp_path / "session-a.json").write_text(json.dumps(SAMPLE_STATUSLINE))
        (tmp_path / "session-b.json").write_text(json.dumps(SAMPLE_STATUSLINE))

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_existing()

        assert "session-a" in watcher.sessions
        assert "session-b" in watcher.sessions

    def test_skips_dotfiles(self, tmp_path):
        """Dotfiles (like .tmp from atomic writes) should be skipped."""
        (tmp_path / ".session-a.tmp").write_text(json.dumps(SAMPLE_STATUSLINE))
        (tmp_path / "session-b.json").write_text(json.dumps(SAMPLE_STATUSLINE))

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_existing()

        assert ".session-a" not in watcher.sessions
        assert "session-b" in watcher.sessions

    def test_skips_non_json_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "session.json").write_text(json.dumps(SAMPLE_STATUSLINE))

        watcher = StatuslineWatcher(monitor_dir=str(tmp_path))
        watcher._read_existing()

        assert "readme" not in watcher.sessions
        assert "session" in watcher.sessions


class TestDeleteHandling:
    """Test session removal on file deletion."""

    def test_remove_session_on_delete(self, tmp_path):
        on_update = MagicMock()
        watcher = StatuslineWatcher(monitor_dir=str(tmp_path), on_update=on_update)
        watcher.sessions["old-session"] = {"cost_usd": 1.0}

        watcher._handle_delete("old-session")

        assert "old-session" not in watcher.sessions
        on_update.assert_called_once_with("old-session", None)

    def test_remove_unknown_session(self, tmp_path):
        """Deleting unknown session should not crash."""
        on_update = MagicMock()
        watcher = StatuslineWatcher(monitor_dir=str(tmp_path), on_update=on_update)

        watcher._handle_delete("nonexistent")  # should not raise
        on_update.assert_called_once_with("nonexistent", None)
