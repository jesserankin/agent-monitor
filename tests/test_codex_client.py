"""Tests for Codex SQLite telemetry mapping."""

import sqlite3
from unittest.mock import patch

from agent_monitor.clients.codex import CodexTelemetryReader, read_latest_thread_metadata, read_live_status
from agent_monitor.models import AgentStatus


def create_state_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            cwd TEXT NOT NULL,
            title TEXT NOT NULL,
            model TEXT,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            updated_at_ms INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


def create_logs_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            ts_nanos INTEGER NOT NULL,
            level TEXT NOT NULL,
            target TEXT NOT NULL,
            feedback_log_body TEXT,
            module_path TEXT,
            thread_id TEXT,
            process_uuid TEXT,
            estimated_bytes INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def insert_log(
    path,
    *,
    ts,
    body,
    thread_id="thread-1",
    process_uuid="pid:111:uuid",
    module_path="codex_api::endpoint::responses_websocket",
):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body, module_path, thread_id, process_uuid)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, 0, "TRACE", module_path or "", body, module_path, thread_id, process_uuid),
    )
    conn.commit()
    conn.close()


def test_reads_latest_thread_metadata_by_cwd(tmp_path):
    db_path = tmp_path / "state.sqlite"
    create_state_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("older", "/repo/project", "Older", "gpt-5.4", 10, 1, 1000),
    )
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("newer", "/repo/project", "Newer", "gpt-5.5", 20, 2, 2000),
    )
    conn.commit()
    conn.close()

    metadata = read_latest_thread_metadata(cwd="/repo/project", db_path=db_path)

    assert metadata is not None
    assert metadata.thread_id == "newer"
    assert metadata.title == "Newer"
    assert metadata.model == "gpt-5.5"
    assert metadata.tokens_used == 20
    assert metadata.updated_at_ms == 2000


def test_read_latest_thread_metadata_closes_sqlite_connection(tmp_path):
    db_path = tmp_path / "state.sqlite"
    db_path.write_text("")

    class FakeResult:
        def fetchone(self):
            return {
                "id": "thread-1",
                "cwd": "/repo/project",
                "title": "Task",
                "model": "gpt-5.5",
                "tokens_used": 10,
                "updated_at_ms": 1000,
            }

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def execute(self, *_args, **_kwargs):
            return FakeResult()

        def close(self):
            self.closed = True

    conn = FakeConnection()
    with patch("agent_monitor.clients.codex._connect_readonly", return_value=conn):
        metadata = read_latest_thread_metadata(cwd="/repo/project", db_path=db_path)

    assert metadata is not None
    assert conn.closed is True


def test_maps_response_created_to_active_since_first_active_event(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body='turn{model=test-model}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.created"}',
    )
    insert_log(
        db_path,
        ts=12,
        module_path="codex_core::session::turn",
        body=(
            'turn{model=test-model}:run_sampling_request{cwd=/repo/project}: '
            "post sampling token usage total_usage_tokens=123 estimated_token_count=50 auto_compact_limit=200"
        ),
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.ACTIVE
    assert status.active_since_ms == 10_000
    assert status.model == "test-model"
    assert status.tokens_used == 123
    assert status.context_used_pct == 25.0


def test_maps_response_completed_to_idle_and_clears_active_since(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.created"}',
    )
    insert_log(
        db_path,
        ts=15,
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.IDLE
    assert status.active_since_ms is None


def test_response_completed_wins_over_embedded_approval_text(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=15,
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed","instructions":"approval request text"}',
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.IDLE


def test_neutral_token_usage_after_completed_keeps_idle_status(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )
    insert_log(
        db_path,
        ts=11,
        module_path="codex_core::session::turn",
        body="turn{model=test-model}:run_turn: post sampling token usage total_usage_tokens=321 estimated_token_count=100 auto_compact_limit=200",
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.IDLE
    assert status.tokens_used == 321
    assert status.context_used_pct == 50.0
    assert status.active_since_ms is None


def test_gpt_55_context_uses_model_window_not_auto_compact_limit(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        module_path="codex_core::session::turn",
        body=(
            "turn{model=gpt-5.5}:run_turn: post sampling token usage "
            "total_usage_tokens=103680 estimated_token_count=100000 auto_compact_limit=244800"
        ),
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.context_used_pct == 27.0


def test_ignores_free_text_context_percentage(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body="assistant message: Context 82% used",
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.context_used_pct is None


def test_ignores_token_usage_text_outside_session_turn_event(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        module_path="codex_api::endpoint::responses_websocket",
        body=(
            "Received message with tool output mentioning "
            "post sampling token usage total_usage_tokens=999 estimated_token_count=200 auto_compact_limit=200"
        ),
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.tokens_used is None
    assert status.context_used_pct is None


def test_context_percentage_is_clamped(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        module_path="codex_core::session::turn",
        body="turn{model=gpt-5.5}:run_turn: post sampling token usage estimated_token_count=250 auto_compact_limit=200",
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.context_used_pct == 100.0


def test_process_noise_after_completed_keeps_idle_status(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        thread_id="wrapped-thread",
        process_uuid="pid:222:wrapped",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )
    insert_log(
        db_path,
        ts=20,
        thread_id=None,
        process_uuid="pid:222:wrapped",
        body="inotify event: unrelated process noise",
    )

    status = read_live_status(cwd="/repo/project", process_pid=222, db_path=db_path)

    assert status is not None
    assert status.thread_id == "wrapped-thread"
    assert status.status == AgentStatus.IDLE


def test_maps_approval_request_to_waiting_approval(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body="turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: approval request pending",
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.WAITING_APPROVAL
    assert status.active_since_ms is None


def test_waiting_approval_wins_over_active_sampling_marker(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body=(
            "turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: "
            "tool approval required; waiting for approval"
        ),
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.WAITING_APPROVAL
    assert status.active_since_ms is None


def test_maps_pending_input_to_waiting_input(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=10,
        body=(
            "turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: "
            "has_pending_input=true"
        ),
    )

    status = read_live_status(cwd="/repo/project", thread_id="thread-1", db_path=db_path)

    assert status is not None
    assert status.status == AgentStatus.WAITING_INPUT
    assert status.active_since_ms is None


def test_telemetry_reader_combines_state_metadata_and_live_status(tmp_path):
    state_db = tmp_path / "state.sqlite"
    logs_db = tmp_path / "logs.sqlite"
    create_state_db(state_db)
    create_logs_db(logs_db)
    conn = sqlite3.connect(state_db)
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("thread-1", "/repo/project", "Task title", "gpt-5.4", 10, 1, 1000),
    )
    conn.commit()
    conn.close()
    insert_log(
        logs_db,
        ts=20,
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.in_progress"}',
    )

    telemetry = CodexTelemetryReader(cwd="/repo/project", state_db_path=state_db, logs_db_path=logs_db).read()

    assert telemetry is not None
    assert telemetry.status == AgentStatus.ACTIVE
    assert telemetry.thread_id == "thread-1"
    assert telemetry.title == "Task title"
    assert telemetry.model == "gpt-5.5"
    assert telemetry.tokens_used == 10
    assert telemetry.active_since_ms == 20_000


def test_live_status_prefers_process_pid_over_newer_same_cwd_thread(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=20,
        thread_id="wrapped-thread",
        process_uuid="pid:222:wrapped",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )
    insert_log(
        db_path,
        ts=30,
        thread_id="other-thread",
        process_uuid="pid:333:other",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.in_progress"}',
    )

    status = read_live_status(cwd="/repo/project", process_pid=222, db_path=db_path)

    assert status is not None
    assert status.thread_id == "wrapped-thread"
    assert status.status == AgentStatus.IDLE


def test_live_status_accepts_process_pid_scope_for_launcher_children(tmp_path):
    db_path = tmp_path / "logs.sqlite"
    create_logs_db(db_path)
    insert_log(
        db_path,
        ts=20,
        thread_id="wrapped-thread",
        process_uuid="pid:333:codex-child",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.in_progress"}',
    )

    status = read_live_status(cwd="/repo/project", process_pids={222, 333}, db_path=db_path)

    assert status is not None
    assert status.thread_id == "wrapped-thread"
    assert status.status == AgentStatus.ACTIVE


def test_telemetry_reader_locks_thread_after_process_match(tmp_path):
    state_db = tmp_path / "state.sqlite"
    logs_db = tmp_path / "logs.sqlite"
    create_state_db(state_db)
    create_logs_db(logs_db)
    conn = sqlite3.connect(state_db)
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("wrapped-thread", "/repo/project", "Wrapped", "gpt-5.5", 20, 2, 2000),
    )
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("other-thread", "/repo/project", "Other", "gpt-5.5", 30, 3, 3000),
    )
    conn.commit()
    conn.close()
    insert_log(
        logs_db,
        ts=20,
        thread_id="wrapped-thread",
        process_uuid="pid:222:wrapped",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )

    reader = CodexTelemetryReader(cwd="/repo/project", process_pid=222, state_db_path=state_db, logs_db_path=logs_db)
    telemetry = reader.read()

    assert telemetry is not None
    assert telemetry.thread_id == "wrapped-thread"
    assert telemetry.title == "Wrapped"
    assert telemetry.status == AgentStatus.IDLE


def test_telemetry_reader_prefers_expected_thread_over_newer_same_cwd_thread(tmp_path):
    state_db = tmp_path / "state.sqlite"
    logs_db = tmp_path / "logs.sqlite"
    create_state_db(state_db)
    create_logs_db(logs_db)
    conn = sqlite3.connect(state_db)
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("expected-thread", "/repo/project", "Expected", "gpt-5.5", 20, 2, 2000),
    )
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("newer-thread", "/repo/project", "Newer", "gpt-5.5", 30, 3, 3000),
    )
    conn.commit()
    conn.close()
    insert_log(
        logs_db,
        ts=20,
        thread_id="expected-thread",
        process_uuid="pid:111:expected",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )
    insert_log(
        logs_db,
        ts=30,
        thread_id="newer-thread",
        process_uuid="pid:333:newer",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.in_progress"}',
    )

    reader = CodexTelemetryReader(
        cwd="/repo/project",
        thread_id="expected-thread",
        state_db_path=state_db,
        logs_db_path=logs_db,
    )
    telemetry = reader.read()

    assert telemetry is not None
    assert telemetry.thread_id == "expected-thread"
    assert telemetry.title == "Expected"
    assert telemetry.status == AgentStatus.IDLE


def test_telemetry_reader_expected_thread_works_before_process_log_match(tmp_path):
    state_db = tmp_path / "state.sqlite"
    logs_db = tmp_path / "logs.sqlite"
    create_state_db(state_db)
    create_logs_db(logs_db)
    conn = sqlite3.connect(state_db)
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("expected-thread", "/repo/project", "Expected", "gpt-5.5", 20, 2, 2000),
    )
    conn.execute(
        "INSERT INTO threads (id, cwd, title, model, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("newer-thread", "/repo/project", "Newer", "gpt-5.5", 30, 3, 3000),
    )
    conn.commit()
    conn.close()
    insert_log(
        logs_db,
        ts=20,
        thread_id="expected-thread",
        process_uuid="pid:333:expected",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.completed"}',
    )
    insert_log(
        logs_db,
        ts=30,
        thread_id="newer-thread",
        process_uuid="pid:333:newer",
        body='turn{model=gpt-5.5}:run_sampling_request{cwd=/repo/project}: websocket event: {"type":"response.in_progress"}',
    )

    reader = CodexTelemetryReader(
        cwd="/repo/project",
        thread_id="expected-thread",
        process_pid=222,
        state_db_path=state_db,
        logs_db_path=logs_db,
    )
    telemetry = reader.read()

    assert telemetry is not None
    assert telemetry.thread_id == "expected-thread"
    assert telemetry.title == "Expected"
    assert telemetry.status == AgentStatus.IDLE
