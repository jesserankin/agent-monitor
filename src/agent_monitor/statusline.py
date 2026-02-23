"""Statusline file watcher for Claude Code sidecar JSON data."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from watchfiles import Change, awatch

from agent_monitor.models import MONITOR_DIR

logger = logging.getLogger(__name__)


def _extract_fields(data: dict) -> dict:
    """Extract relevant statusline fields from raw JSON."""
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
    context = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    model = data.get("model") if isinstance(data.get("model"), dict) else {}

    return {
        "cost_usd": cost.get("total_cost_usd"),
        "duration_ms": cost.get("total_duration_ms"),
        "context_used_pct": context.get("used_percentage"),
        "model_name": model.get("display_name"),
        "lines_added": cost.get("total_lines_added"),
        "lines_removed": cost.get("total_lines_removed"),
    }


class StatuslineWatcher:
    """Watches $XDG_RUNTIME_DIR/claude-monitor/ for statusline JSON files.

    Attributes:
        sessions: Extracted statusline data keyed by session name (filename stem).
        on_update: Callback(name, data) when data changes. data is None on delete.
    """

    def __init__(
        self,
        monitor_dir: str = MONITOR_DIR,
        on_update: Callable[[str, dict | None], None] | None = None,
    ) -> None:
        self.sessions: dict[str, dict] = {}
        self.on_update = on_update
        self._monitor_dir = Path(monitor_dir)

    async def watch(self) -> None:
        """Watch the monitor directory for JSON file changes.

        Creates the directory if needed, reads existing files, then watches
        for changes indefinitely. This coroutine runs until cancelled.
        """
        self._monitor_dir.mkdir(parents=True, exist_ok=True)
        self._read_existing()

        async for changes in awatch(str(self._monitor_dir), debounce=400, recursive=False):
            for change_type, filepath in changes:
                path = Path(filepath)
                if not path.name.endswith(".json") or path.name.startswith("."):
                    continue

                if change_type in (Change.added, Change.modified):
                    self._read_file(path)
                elif change_type == Change.deleted:
                    name = path.stem
                    self._handle_delete(name)

    def _read_existing(self) -> None:
        """Read all existing .json files in the monitor directory."""
        if not self._monitor_dir.exists():
            return
        for path in self._monitor_dir.glob("*.json"):
            if path.name.startswith("."):
                continue
            self._read_file(path)

    def _read_file(self, path: Path) -> None:
        """Read and parse a single statusline JSON file.

        Handles JSONDecodeError and FileNotFoundError gracefully (race with
        atomic write via tmp+mv pattern).
        """
        name = path.stem
        try:
            content = path.read_text()
        except FileNotFoundError:
            logger.debug("Statusline file vanished before read: %s", path)
            return
        except OSError as exc:
            logger.warning("Error reading statusline file %s: %s", path, exc)
            return

        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            logger.debug("Malformed JSON in statusline file: %s", path)
            return

        if not isinstance(raw, dict):
            logger.debug("Statusline JSON is not an object: %s", path)
            return

        data = _extract_fields(raw)
        self.sessions[name] = data

        if self.on_update:
            self.on_update(name, data)

    def _handle_delete(self, name: str) -> None:
        """Handle deletion of a statusline file."""
        self.sessions.pop(name, None)
        if self.on_update:
            self.on_update(name, None)
