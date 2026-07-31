from app.db import conversation_store as store


def test_create_conversation_and_round_trip_messages(isolated_app_db):
    conversation_id = store.create_conversation("Process payroll for a given pay period")
    store.add_user_message(conversation_id, "Process payroll for a given pay period")
    store.add_assistant_message(
        conversation_id,
        full_script="-- SECTION: MAIN_SCRIPT\nSELECT 1;",
        sections={"main_script": "SELECT 1;"},
        retrieved_context={"schema_chunks": [], "pattern_chunks": []},
        sql_valid=True,
        sql_issues=[],
    )

    conversation = store.get_conversation(conversation_id)

    assert conversation["title"] == "Process payroll for a given pay period"
    assert len(conversation["messages"]) == 2
    assert conversation["messages"][0]["role"] == "user"
    assert conversation["messages"][1]["role"] == "assistant"
    assert conversation["messages"][1]["kind"] == "generation"
    assert conversation["messages"][1]["sql_valid"] is True


def test_long_first_prompt_gets_truncated_into_title(isolated_app_db):
    long_prompt = "a" * 100
    conversation_id = store.create_conversation(long_prompt)

    conversation = store.get_conversation(conversation_id)

    assert len(conversation["title"]) <= 60
    assert conversation["title"].endswith("…")


def test_rejected_message_has_no_generation_fields(isolated_app_db):
    conversation_id = store.create_conversation("write me a haiku")
    store.add_user_message(conversation_id, "write me a haiku")
    store.add_rejected_message(conversation_id, "Not a stored procedure request.")

    conversation = store.get_conversation(conversation_id)
    rejected_message = conversation["messages"][1]

    assert rejected_message["kind"] == "rejected"
    assert rejected_message["content"] == "Not a stored procedure request."
    assert "full_script" not in rejected_message


def test_list_conversations_reports_correct_message_count(isolated_app_db):
    conversation_id = store.create_conversation("Approve a timesheet")
    store.add_user_message(conversation_id, "Approve a timesheet")
    store.add_assistant_message(
        conversation_id,
        full_script="...",
        sections={},
        retrieved_context={},
        sql_valid=True,
        sql_issues=[],
    )

    conversations = store.list_conversations()

    assert len(conversations) == 1
    assert conversations[0]["message_count"] == 2


def test_delete_conversation_removes_messages_before_the_conversation_row(isolated_app_db):
    """Regression test: an earlier version deleted the parent `conversations`
    row before its child `messages` rows, which violates the FK constraint
    (PRAGMA foreign_keys = ON) and raised sqlite3.IntegrityError. This test
    exists specifically so that ordering bug can't come back unnoticed."""
    conversation_id = store.create_conversation("Onboard a new employee")
    store.add_user_message(conversation_id, "Onboard a new employee")
    store.add_assistant_message(
        conversation_id,
        full_script="...",
        sections={},
        retrieved_context={},
        sql_valid=True,
        sql_issues=[],
    )

    deleted = store.delete_conversation(conversation_id)

    assert deleted is True
    assert store.get_conversation(conversation_id) is None
    assert store.list_conversations() == []


def test_delete_nonexistent_conversation_returns_false(isolated_app_db):
    assert store.delete_conversation("does-not-exist") is False


def test_get_last_user_message_returns_most_recent(isolated_app_db):
    conversation_id = store.create_conversation("first prompt")
    store.add_user_message(conversation_id, "first prompt")
    store.add_assistant_message(
        conversation_id, full_script="...", sections={}, retrieved_context={}, sql_valid=True, sql_issues=[]
    )
    store.add_user_message(conversation_id, "second prompt")

    last_user_message = store.get_last_user_message(conversation_id)

    assert last_user_message["content"] == "second prompt"
