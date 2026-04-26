"""Codex SQLite telemetry reader.

This module treats Codex logs as an optional rich signal. The sidecar remains
the stable contract consumed by the rest of agent-monitor.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_monitor.models import AgentStatus

RESPONSE_EVENT_RE = re.compile(r'"type"\s*:\s*"(response\.[a-z_]+)"')
MODEL_RE = re.compile(r"\bmodel=([A-Za-z0-9_.:-]+)")
TOKEN_USAGE_RE = re.compile(r"\btotal_usage_tokens=(\d+)")
ESTIMATED_TOKEN_RE = re.compile(r"\bestimated_token_count=(?:Some\()?(\d+)\)?")
AUTO_COMPACT_LIMIT_RE = re.compile(r"\bauto_compact_limit=(\d+)")
MODEL_CONTEXT_LIMITS = {
    "gpt-5.5": 384_000,
}

ACTIVE_RESPONSE_EVENTS = {"response.created", "response.in_progress", "response.output_item.added"}
IDLE_RESPONSE_EVENTS = {"response.completed"}
ERROR_RESPONSE_EVENTS = {"response.failed", "response.incomplete"}


@dataclass(frozen=True)
class CodexThreadMetadata:
    thread_id: str
    cwd: str
    title: str | None = None
    model: str | None = None
    tokens_used: int | None = None
    updated_at_ms: int | None = None


@dataclass(frozen=True)
class CodexLiveStatus:
    status: AgentStatus
    thread_id: str | None = None
    model: str | None = None
    tokens_used: int | None = None
    updated_at_ms: int | None = None
    active_since_ms: int | None = None
    context_used_pct: float | None = None


@dataclass(frozen=True)
class CodexTelemetry:
    status: AgentStatus | None = None
    thread_id: str | None = None
    title: str | None = None
    model: str | None = None
    tokens_used: int | None = None
    updated_at_ms: int | None = None
    active_since_ms: int | None = None
    context_used_pct: float | None = None


class CodexTelemetryReader:
    """Read best-effort Codex status from local Codex SQLite databases."""

    def __init__(
        self,
        *,
        cwd: str | None = None,
        thread_id: str | None = None,
        process_pid: int | None = None,
        state_db_path: str | Path | None = None,
        logs_db_path: str | Path | None = None,
        recent_limit: int = 1000,
    ) -> None:
        self.cwd = _normalize_cwd(cwd)
        self.thread_id = thread_id
        self.process_pid = process_pid
        self.state_db_path = Path(state_db_path) if state_db_path is not None else Path.home() / ".codex" / "state_5.sqlite"
        self.logs_db_path = Path(logs_db_path) if logs_db_path is not None else Path.home() / ".codex" / "logs_2.sqlite"
        self.recent_limit = recent_limit

    def read(self) -> CodexTelemetry | None:
        live = read_live_status(
            cwd=self.cwd,
            thread_id=self.thread_id,
            process_pids=_process_pid_scope(self.process_pid),
            db_path=self.logs_db_path,
            limit=self.recent_limit,
        )
        if self.thread_id is None and live and live.thread_id:
            self.thread_id = live.thread_id

        if self.process_pid is not None and self.thread_id is None and live is None:
            return None

        metadata = read_latest_thread_metadata(
            cwd=self.cwd,
            thread_id=self.thread_id,
            db_path=self.state_db_path,
        )
        if self.thread_id is None and metadata:
            self.thread_id = metadata.thread_id
        thread_id = self.thread_id or (live.thread_id if live else None)

        if metadata is None and live is None:
            return None

        return CodexTelemetry(
            status=live.status if live else None,
            thread_id=thread_id or (live.thread_id if live else None),
            title=metadata.title if metadata else None,
            model=(live.model if live and live.model else metadata.model if metadata else None),
            tokens_used=(
                live.tokens_used
                if live and live.tokens_used is not None
                else metadata.tokens_used
                if metadata
                else None
            ),
            updated_at_ms=(
                live.updated_at_ms
                if live and live.updated_at_ms is not None
                else metadata.updated_at_ms
                if metadata
                else None
            ),
            active_since_ms=live.active_since_ms if live else None,
            context_used_pct=live.context_used_pct if live else None,
        )


def read_latest_thread_metadata(
    *,
    cwd: str | None,
    thread_id: str | None = None,
    db_path: str | Path | None = None,
) -> CodexThreadMetadata | None:
    """Return latest Codex thread metadata for a cwd or explicit thread id."""
    path = Path(db_path) if db_path is not None else Path.home() / ".codex" / "state_5.sqlite"
    if not path.exists():
        return None

    where = "id = ?"
    params: tuple[Any, ...]
    if thread_id:
        params = (thread_id,)
    elif cwd:
        where = "cwd = ?"
        params = (_normalize_cwd(cwd),)
    else:
        return None

    try:
        with closing(_connect_readonly(path)) as conn:
            row = conn.execute(
                f"""
                SELECT id, cwd, title, model, tokens_used, updated_at_ms
                FROM threads
                WHERE {where}
                ORDER BY updated_at_ms DESC, updated_at DESC, id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    return CodexThreadMetadata(
        thread_id=str(row["id"]),
        cwd=str(row["cwd"]),
        title=_optional_str(row["title"]),
        model=_optional_str(row["model"]),
        tokens_used=_optional_int(row["tokens_used"]),
        updated_at_ms=_optional_int(row["updated_at_ms"]),
    )


def read_live_status(
    *,
    cwd: str | None,
    thread_id: str | None = None,
    process_pid: int | None = None,
    process_pids: set[int] | None = None,
    db_path: str | Path | None = None,
    limit: int = 1000,
) -> CodexLiveStatus | None:
    """Map recent Codex log rows to a coarse live status."""
    path = Path(db_path) if db_path is not None else Path.home() / ".codex" / "logs_2.sqlite"
    if not path.exists():
        return None

    try:
        pid_scope = process_pids or ({process_pid} if process_pid is not None else None)
        rows = _read_log_rows(path, thread_id=thread_id, process_pids=pid_scope, limit=limit)
    except sqlite3.Error:
        return None

    cwd_marker = f"cwd={_normalize_cwd(cwd)}" if cwd else None
    process_uuid_prefixes = {f"pid:{pid}:" for pid in pid_scope} if pid_scope else None
    events = [
        _event_from_row(row)
        for row in rows
        if _row_matches(
            row,
            thread_id=thread_id,
            cwd_marker=cwd_marker,
            process_uuid_prefixes=process_uuid_prefixes,
        )
    ]
    events = [event for event in events if event is not None]
    if not events:
        return None

    events.sort(key=lambda event: (event.ts, event.ts_nanos, event.id))
    return _status_from_events(events)


@dataclass(frozen=True)
class _LogEvent:
    id: int
    ts: int
    ts_nanos: int
    thread_id: str | None
    process_uuid: str | None
    module_path: str | None
    body: str
    response_event: str | None

    @property
    def timestamp_ms(self) -> int:
        return self.ts * 1000 + self.ts_nanos // 1_000_000


def _read_log_rows(path: Path, *, thread_id: str | None, process_pids: set[int] | None, limit: int) -> list[sqlite3.Row]:
    with closing(_connect_readonly(path)) as conn:
        if process_pids:
            predicates = " OR ".join("process_uuid LIKE ?" for _pid in process_pids)
            identity_predicate = f"({predicates})"
            identity_params: tuple[Any, ...] = tuple(f"pid:{pid}:%" for pid in process_pids)
            if thread_id:
                identity_predicate = f"(thread_id = ? OR {predicates})"
                identity_params = (thread_id, *identity_params)
            return list(
                conn.execute(
                    f"""
                    SELECT id, ts, ts_nanos, thread_id, process_uuid, module_path, feedback_log_body
                    FROM logs
                    WHERE {identity_predicate}
                      AND (
                        thread_id IS NOT NULL
                        OR feedback_log_body LIKE '%cwd=%'
                        OR feedback_log_body LIKE '%response.%'
                        OR feedback_log_body LIKE '%post sampling token usage%'
                        OR feedback_log_body LIKE '%approval%'
                        OR feedback_log_body LIKE '%waiting%'
                        OR feedback_log_body LIKE '%pending_input%'
                      )
                    ORDER BY ts DESC, ts_nanos DESC, id DESC
                    LIMIT ?
                    """,
                    (*identity_params, limit),
                )
            )
        if thread_id:
            return list(
                conn.execute(
                    """
                    SELECT id, ts, ts_nanos, thread_id, process_uuid, module_path, feedback_log_body
                    FROM logs
                    WHERE thread_id = ?
                    ORDER BY ts DESC, ts_nanos DESC, id DESC
                    LIMIT ?
                    """,
                    (thread_id, limit),
                )
            )
        return list(
            conn.execute(
                """
                SELECT id, ts, ts_nanos, thread_id, process_uuid, module_path, feedback_log_body
                FROM logs
                ORDER BY ts DESC, ts_nanos DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def _event_from_row(row: sqlite3.Row) -> _LogEvent | None:
    body = _optional_str(row["feedback_log_body"]) or ""
    if not body:
        return None
    response_event_match = RESPONSE_EVENT_RE.search(body)
    return _LogEvent(
        id=int(row["id"]),
        ts=int(row["ts"]),
        ts_nanos=int(row["ts_nanos"]),
        thread_id=_optional_str(row["thread_id"]),
        process_uuid=_optional_str(row["process_uuid"]),
        module_path=_optional_str(row["module_path"]),
        body=body,
        response_event=response_event_match.group(1) if response_event_match else None,
    )


def _row_matches(
    row: sqlite3.Row,
    *,
    thread_id: str | None,
    cwd_marker: str | None,
    process_uuid_prefixes: set[str] | None,
) -> bool:
    process_uuid = _optional_str(row["process_uuid"])
    if process_uuid_prefixes and process_uuid and any(process_uuid.startswith(prefix) for prefix in process_uuid_prefixes):
        body = _optional_str(row["feedback_log_body"]) or ""
        return (
            _optional_str(row["thread_id"]) is not None
            or (cwd_marker is not None and cwd_marker in body)
            or "response." in body
            or "post sampling token usage" in body
            or _is_waiting_approval(body)
            or _is_waiting_input(body)
        )
    row_thread_id = _optional_str(row["thread_id"])
    if thread_id and row_thread_id == thread_id:
        return True
    if cwd_marker:
        body = _optional_str(row["feedback_log_body"]) or ""
        return cwd_marker in body
    return False


def _status_from_events(events: list[_LogEvent]) -> CodexLiveStatus:
    latest = events[-1]
    status_event = _latest_status_event(events)
    status = AgentStatus.RUNNING
    active_since_ms: int | None = None

    if status_event is not None:
        status = _status_for_event(status_event)
    if status == AgentStatus.ACTIVE:
        active_since_ms = _active_since_ms(events)

    model = _latest_regex_group(events, MODEL_RE)
    token_events = [event for event in events if _is_token_usage_event(event)]
    tokens_used = _latest_int_regex_group(token_events, TOKEN_USAGE_RE)
    context_used_pct = _latest_context_used_pct(token_events, model=model, tokens_used=tokens_used)

    return CodexLiveStatus(
        status=status,
        thread_id=latest.thread_id,
        model=model,
        tokens_used=tokens_used,
        updated_at_ms=latest.timestamp_ms,
        active_since_ms=active_since_ms,
        context_used_pct=context_used_pct,
    )


def _latest_status_event(events: list[_LogEvent]) -> _LogEvent | None:
    for event in reversed(events):
        if _status_for_event(event) != AgentStatus.RUNNING:
            return event
    return None


def _status_for_event(event: _LogEvent) -> AgentStatus:
    if event.response_event in ERROR_RESPONSE_EVENTS:
        return AgentStatus.ERROR
    if event.response_event in IDLE_RESPONSE_EVENTS:
        return AgentStatus.IDLE
    if _is_waiting_approval(event.body):
        return AgentStatus.WAITING_APPROVAL
    if _is_waiting_input(event.body):
        return AgentStatus.WAITING_INPUT
    if event.response_event in ACTIVE_RESPONSE_EVENTS or _is_active_turn(event.body):
        return AgentStatus.ACTIVE
    return AgentStatus.RUNNING


def _active_since_ms(events: list[_LogEvent]) -> int:
    last_idle_index = -1
    for index, event in enumerate(events):
        if event.response_event in IDLE_RESPONSE_EVENTS or event.response_event in ERROR_RESPONSE_EVENTS:
            last_idle_index = index
    for event in events[last_idle_index + 1 :]:
        if event.response_event in ACTIVE_RESPONSE_EVENTS or _is_active_turn(event.body):
            return event.timestamp_ms
    return events[-1].timestamp_ms


def _is_active_turn(body: str) -> bool:
    return (
        "turn{" in body
        and "run_sampling_request" in body
        and not _is_waiting_approval(body)
        and not _is_waiting_input(body)
    )


def _is_waiting_approval(body: str) -> bool:
    lower = body.lower()
    if "approval" not in lower:
        return False
    if "decision: approved" in lower or "decision=approved" in lower:
        return False
    return any(marker in lower for marker in ("pending", "request", "required", "waiting"))


def _is_waiting_input(body: str) -> bool:
    lower = body.lower()
    return any(
        marker in lower
        for marker in (
            "waiting for user input",
            "user input required",
            "waiting_input",
            "has_pending_input=true",
        )
    )


def _latest_regex_group(events: list[_LogEvent], pattern: re.Pattern[str]) -> str | None:
    for event in reversed(events):
        match = pattern.search(event.body)
        if match:
            return match.group(1)
    return None


def _latest_int_regex_group(events: list[_LogEvent], pattern: re.Pattern[str]) -> int | None:
    value = _latest_regex_group(events, pattern)
    return int(value) if value is not None else None


def _latest_context_used_pct(events: list[_LogEvent], *, model: str | None, tokens_used: int | None) -> float | None:
    context_limit = _context_limit_for_model(model)
    if tokens_used is not None and context_limit is not None:
        return _clamp_context_pct(round((tokens_used / context_limit) * 100, 1))

    estimated_tokens = _latest_int_regex_group(events, ESTIMATED_TOKEN_RE)
    auto_compact_limit = _latest_int_regex_group(events, AUTO_COMPACT_LIMIT_RE)
    if estimated_tokens is not None and auto_compact_limit:
        return _clamp_context_pct(round((estimated_tokens / auto_compact_limit) * 100, 1))
    return None


def _clamp_context_pct(value: float) -> float:
    return max(0.0, min(100.0, value))


def _context_limit_for_model(model: str | None) -> int | None:
    if model is None:
        return None
    return MODEL_CONTEXT_LIMITS.get(model)


def _is_token_usage_event(event: _LogEvent) -> bool:
    return event.module_path == "codex_core::session::turn" and "post sampling token usage" in event.body


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.2)
    conn.row_factory = sqlite3.Row
    return conn


def _process_pid_scope(process_pid: int | None) -> set[int] | None:
    if process_pid is None:
        return None
    pids = {process_pid}
    pending = [process_pid]
    while pending:
        parent = pending.pop()
        for child in _child_pids(parent):
            if child not in pids:
                pids.add(child)
                pending.append(child)
    return pids


def _child_pids(parent_pid: int) -> list[int]:
    children_path = Path("/proc") / str(parent_pid) / "task" / str(parent_pid) / "children"
    try:
        raw = children_path.read_text().strip()
    except OSError:
        return []
    pids: list[int] = []
    for part in raw.split():
        try:
            pids.append(int(part))
        except ValueError:
            continue
    return pids


def _normalize_cwd(cwd: str | None) -> str | None:
    if cwd is None:
        return None
    return os.path.realpath(os.path.expanduser(cwd))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
