"""Basic telemetry for the RAG pipeline (backend/data/app.db): one row per
generation request, capturing what a minimal monitoring setup needs --
acceptance/rejection rate, latency per pipeline stage, retrieval results,
and output validity. Not a replacement for real observability tooling
(no tracing, no percentiles, no alerting) -- just enough to answer "is this
working, and how well" without leaving the codebase."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    id                        TEXT PRIMARY KEY,
    conversation_id           TEXT,
    message_id                TEXT,
    prompt                    TEXT NOT NULL,
    is_regenerate             INTEGER NOT NULL,
    accepted                  INTEGER NOT NULL,
    rejection_reason          TEXT,
    retrieved_tables_json     TEXT,
    pattern_count             INTEGER,
    classify_latency_ms       REAL,
    retrieval_latency_ms      REAL,
    generation_latency_ms     REAL,
    total_latency_ms          REAL,
    sql_valid                 INTEGER,
    sql_issues_json           TEXT,
    error_message             TEXT,
    created_at                TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.APP_DB_PATH)
    connection.executescript(_SCHEMA)
    return connection


def log_event(
    *,
    conversation_id: str | None,
    message_id: str | None,
    prompt: str,
    is_regenerate: bool,
    accepted: bool,
    rejection_reason: str | None = None,
    retrieved_tables: list[dict] | None = None,
    pattern_count: int | None = None,
    classify_latency_ms: float | None = None,
    retrieval_latency_ms: float | None = None,
    generation_latency_ms: float | None = None,
    total_latency_ms: float | None = None,
    sql_valid: bool | None = None,
    sql_issues: list[str] | None = None,
    error_message: str | None = None,
) -> None:
    connection = _connect()
    with connection:
        connection.execute(
            "INSERT INTO telemetry_events "
            "(id, conversation_id, message_id, prompt, is_regenerate, accepted, rejection_reason, "
            " retrieved_tables_json, pattern_count, classify_latency_ms, retrieval_latency_ms, "
            " generation_latency_ms, total_latency_ms, sql_valid, sql_issues_json, error_message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                conversation_id,
                message_id,
                prompt,
                int(is_regenerate),
                int(accepted),
                rejection_reason,
                json.dumps(retrieved_tables) if retrieved_tables is not None else None,
                pattern_count,
                classify_latency_ms,
                retrieval_latency_ms,
                generation_latency_ms,
                total_latency_ms,
                None if sql_valid is None else int(sql_valid),
                json.dumps(sql_issues) if sql_issues is not None else None,
                error_message,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    connection.close()


def _avg(connection: sqlite3.Connection, column: str, where: str = "") -> float | None:
    row = connection.execute(f"SELECT AVG({column}) FROM telemetry_events {where}").fetchone()
    return row[0]


def get_metrics_summary() -> dict:
    connection = _connect()

    total = connection.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    accepted = connection.execute("SELECT COUNT(*) FROM telemetry_events WHERE accepted = 1").fetchone()[0]
    rejected = connection.execute("SELECT COUNT(*) FROM telemetry_events WHERE accepted = 0 AND error_message IS NULL").fetchone()[0]
    errored = connection.execute("SELECT COUNT(*) FROM telemetry_events WHERE error_message IS NOT NULL").fetchone()[0]
    regenerated = connection.execute("SELECT COUNT(*) FROM telemetry_events WHERE is_regenerate = 1").fetchone()[0]

    sql_valid_count = connection.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE accepted = 1 AND sql_valid = 1"
    ).fetchone()[0]
    sql_checked_count = connection.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE accepted = 1 AND sql_valid IS NOT NULL"
    ).fetchone()[0]

    table_counts: dict[str, int] = {}
    for (retrieved_json,) in connection.execute(
        "SELECT retrieved_tables_json FROM telemetry_events WHERE retrieved_tables_json IS NOT NULL"
    ):
        for entry in json.loads(retrieved_json):
            table_counts[entry["table_name"]] = table_counts.get(entry["table_name"], 0) + 1

    summary = {
        "total_requests": total,
        "accepted": accepted,
        "rejected": rejected,
        "errored": errored,
        "regenerated": regenerated,
        "rejection_rate": round(rejected / total, 3) if total else None,
        "sql_valid_rate": round(sql_valid_count / sql_checked_count, 3) if sql_checked_count else None,
        "avg_classify_latency_ms": _avg(connection, "classify_latency_ms"),
        "avg_retrieval_latency_ms": _avg(connection, "retrieval_latency_ms"),
        "avg_generation_latency_ms": _avg(connection, "generation_latency_ms"),
        "avg_total_latency_ms": _avg(connection, "total_latency_ms"),
        "most_retrieved_tables": [list(item) for item in sorted(table_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]],
    }
    connection.close()
    return summary
