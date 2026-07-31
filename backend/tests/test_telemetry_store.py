from app.db import telemetry_store as telemetry


def test_empty_metrics_summary_has_no_division_by_zero(isolated_app_db):
    summary = telemetry.get_metrics_summary()

    assert summary["total_requests"] == 0
    assert summary["rejection_rate"] is None
    assert summary["sql_valid_rate"] is None
    assert summary["most_retrieved_tables"] == []


def test_metrics_summary_aggregates_accepted_and_rejected_correctly(isolated_app_db):
    telemetry.log_event(
        conversation_id="c1",
        message_id="m1",
        prompt="Process payroll for a given pay period",
        is_regenerate=False,
        accepted=True,
        retrieved_tables=[{"table_name": "payroll_runs", "score": 0.28}, {"table_name": "employees", "score": 0.34}],
        pattern_count=6,
        classify_latency_ms=2000,
        retrieval_latency_ms=800,
        generation_latency_ms=40000,
        total_latency_ms=43000,
        sql_valid=True,
        sql_issues=[],
    )
    telemetry.log_event(
        conversation_id="c2",
        message_id="m2",
        prompt="Onboard a new employee",
        is_regenerate=False,
        accepted=True,
        retrieved_tables=[{"table_name": "employees", "score": 0.31}],
        pattern_count=6,
        classify_latency_ms=3000,
        retrieval_latency_ms=900,
        generation_latency_ms=50000,
        total_latency_ms=54000,
        sql_valid=False,
        sql_issues=["Unbalanced parentheses."],
    )
    telemetry.log_event(
        conversation_id="c3",
        message_id="m3",
        prompt="write me a haiku",
        is_regenerate=False,
        accepted=False,
        rejection_reason="Not a stored procedure request.",
        classify_latency_ms=2500,
        total_latency_ms=2500,
    )

    summary = telemetry.get_metrics_summary()

    assert summary["total_requests"] == 3
    assert summary["accepted"] == 2
    assert summary["rejected"] == 1
    assert summary["rejection_rate"] == round(1 / 3, 3)
    assert summary["sql_valid_rate"] == 0.5  # 1 of 2 accepted generations was valid
    assert summary["avg_classify_latency_ms"] == (2000 + 3000 + 2500) / 3
    # employees appears in both accepted requests -> should rank first
    assert summary["most_retrieved_tables"][0] == ["employees", 2]


def test_errored_request_counts_separately_from_rejected(isolated_app_db):
    telemetry.log_event(
        conversation_id="c1",
        message_id=None,
        prompt="Process payroll",
        is_regenerate=False,
        accepted=False,
        error_message="Gemini API timed out",
        total_latency_ms=5000,
    )

    summary = telemetry.get_metrics_summary()

    assert summary["total_requests"] == 1
    assert summary["errored"] == 1
    assert summary["rejected"] == 0  # errors are not counted as classifier rejections
