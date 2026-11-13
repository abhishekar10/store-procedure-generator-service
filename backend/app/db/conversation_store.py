"""Read/write access to chat conversations and their messages
(backend/data/app.db) -- kept separate from db/reference.db."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                      TEXT PRIMARY KEY,
    conversation_id         TEXT NOT NULL,
    role                    TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    kind                    TEXT CHECK (kind IS NULL OR kind IN ('generation', 'rejected')),
    content                 TEXT NOT NULL,
    full_script             TEXT,
    sections_json           TEXT,
    retrieved_context_json  TEXT,
    sql_valid               INTEGER,
    sql_issues_json         TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations (id)
);
"""

_TITLE_MAX_LEN = 60


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.app_db_path)
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.executescript(_SCHEMA)
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_conversation(first_prompt: str) -> str:
    conversation_id = uuid.uuid4().hex
    title = first_prompt if len(first_prompt) <= _TITLE_MAX_LEN else first_prompt[: _TITLE_MAX_LEN - 1] + "…"

    connection = _connect()
    with connection:
        connection.execute(
            "INSERT INTO conversations (id, title, created_at) VALUES (?, ?, ?)",
            (conversation_id, title, _now()),
        )
    connection.close()
    return conversation_id


def add_user_message(conversation_id: str, content: str) -> str:
    message_id = uuid.uuid4().hex
    connection = _connect()
    with connection:
        connection.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
            (message_id, conversation_id, content, _now()),
        )
    connection.close()
    return message_id


def add_assistant_message(
    conversation_id: str,
    full_script: str,
    sections: dict,
    retrieved_context: dict,
    sql_valid: bool,
    sql_issues: list[str],
) -> str:
    message_id = uuid.uuid4().hex
    connection = _connect()
    with connection:
        connection.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, kind, content, full_script, sections_json, "
            " retrieved_context_json, sql_valid, sql_issues_json, created_at) "
            "VALUES (?, ?, 'assistant', 'generation', ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                conversation_id,
                full_script,
                full_script,
                json.dumps(sections),
                json.dumps(retrieved_context),
                int(sql_valid),
                json.dumps(sql_issues),
                _now(),
            ),
        )
    connection.close()
    return message_id


def add_rejected_message(conversation_id: str, reason: str) -> str:
    message_id = uuid.uuid4().hex
    connection = _connect()
    with connection:
        connection.execute(
            "INSERT INTO messages (id, conversation_id, role, kind, content, created_at) "
            "VALUES (?, ?, 'assistant', 'rejected', ?, ?)",
            (message_id, conversation_id, reason, _now()),
        )
    connection.close()
    return message_id


def _row_to_message(row: tuple) -> dict:
    (
        message_id,
        role,
        kind,
        content,
        full_script,
        sections_json,
        retrieved_context_json,
        sql_valid,
        sql_issues_json,
        created_at,
    ) = row
    message = {
        "id": message_id,
        "role": role,
        "content": content,
        "created_at": created_at,
    }
    if role == "assistant":
        message["kind"] = kind or "generation"
        if message["kind"] == "generation":
            message["full_script"] = full_script
            message["sections"] = json.loads(sections_json)
            message["retrieved_context"] = json.loads(retrieved_context_json)
            message["sql_valid"] = bool(sql_valid)
            message["sql_issues"] = json.loads(sql_issues_json)
    return message


def get_conversation_messages(conversation_id: str) -> list[dict]:
    connection = _connect()
    rows = connection.execute(
        "SELECT id, role, kind, content, full_script, sections_json, retrieved_context_json, "
        "sql_valid, sql_issues_json, created_at "
        "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    connection.close()
    return [_row_to_message(row) for row in rows]


def list_conversations() -> list[dict]:
    connection = _connect()
    rows = connection.execute(
        "SELECT c.id, c.title, c.created_at, COUNT(m.id) "
        "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
        "GROUP BY c.id ORDER BY c.created_at DESC"
    ).fetchall()
    connection.close()
    return [{"id": row[0], "title": row[1], "created_at": row[2], "message_count": row[3]} for row in rows]


def get_conversation(conversation_id: str) -> dict | None:
    connection = _connect()
    row = connection.execute(
        "SELECT id, title, created_at FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "title": row[1],
        "created_at": row[2],
        "messages": get_conversation_messages(conversation_id),
    }


def delete_conversation(conversation_id: str) -> bool:
    """Deletes a conversation and its messages. Returns False if the
    conversation didn't exist."""
    connection = _connect()
    with connection:
        connection.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        cursor = connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        existed = cursor.rowcount > 0
    connection.close()
    return existed


def get_last_user_message(conversation_id: str) -> dict | None:
    connection = _connect()
    row = connection.execute(
        "SELECT id, role, kind, content, full_script, sections_json, retrieved_context_json, "
        "sql_valid, sql_issues_json, created_at "
        "FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    connection.close()
    return _row_to_message(row) if row else None
