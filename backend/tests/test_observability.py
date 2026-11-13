import json
import logging

import httpx
import pytest
from google.genai import errors as genai_errors

from app.observability import JsonFormatter, emit_generation_metrics, prompt_fingerprint, request_id_var
from app.rag.retry import call_with_retry
from app.ratelimit import TokenBucketLimiter


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def capture():
    handler = _Capture()
    logger = logging.getLogger("app.metrics")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


def test_json_log_line_carries_request_id_and_extra_fields(capture):
    token = request_id_var.set("req-1")
    try:
        logging.getLogger("app.metrics").info("hello", extra={"tables": ["employees"]})
    finally:
        request_id_var.reset(token)

    line = capture.lines[0]
    assert line["message"] == "hello" and line["request_id"] == "req-1" and line["tables"] == ["employees"]
    assert line["level"] == "INFO" and line["ts"].endswith("Z")


def test_emf_line_is_valid_embedded_metric_format(capture):
    emit_generation_metrics(
        endpoint="generate_stream",
        outcome="accepted",
        total_latency_ms=150_000.04,
        classify_latency_ms=4_700,
        retrieval_latency_ms=600,
        generation_latency_ms=144_000,
        sql_valid=True,
        retrieved_tables=[{"table_name": "employees", "score": 0.12}],
    )

    line = capture.lines[0]
    directive = line["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "SPGenerator"
    assert directive["Dimensions"] == [["Endpoint", "Outcome"]]
    # Every declared metric and dimension must be present at the top level.
    for metric in directive["Metrics"]:
        assert isinstance(line[metric["Name"]], (int, float))
    assert line["Endpoint"] == "generate_stream" and line["Outcome"] == "accepted"
    assert line["SqlValid"] == 1 and line["RetrievedTables"] == 1 and line["TotalLatency"] == 150000.0
    assert isinstance(line["_aws"]["Timestamp"], int)


def test_emf_omits_metrics_that_were_not_measured(capture):
    emit_generation_metrics(endpoint="generate", outcome="error", total_latency_ms=10, error_code="upstream_timeout")

    names = {metric["Name"] for metric in capture.lines[0]["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert names == {"Requests", "TotalLatency"}
    assert capture.lines[0]["error_code"] == "upstream_timeout"


def test_prompt_fingerprint_never_contains_the_prompt():
    fingerprint = prompt_fingerprint("secret salary data for alice")

    assert fingerprint["prompt_chars"] == 28
    assert "alice" not in json.dumps(fingerprint)


# --- retry policy -----------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _failing(outcomes, clock, seconds_per_call):
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        clock.now += seconds_per_call
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return call, calls


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setenv("GENERATION_RETRY_BACKOFF_S", "0")


def test_fast_retryable_failure_is_retried():
    clock = _Clock()
    call, calls = _failing([genai_errors.ServerError(503, {}), "ok"], clock, seconds_per_call=1)

    assert call_with_retry(call, attempts=2, clock=clock) == "ok"
    assert calls["n"] == 2


def test_slow_failure_is_not_retried_even_if_retryable():
    clock = _Clock()
    call, calls = _failing([genai_errors.ServerError(500, {}), "ok"], clock, seconds_per_call=200)

    with pytest.raises(genai_errors.ServerError):
        call_with_retry(call, attempts=2, clock=clock)
    assert calls["n"] == 1


@pytest.mark.parametrize(
    "exc", [genai_errors.ClientError(400, {}), genai_errors.ServerError(504, {}), httpx.ReadTimeout("t")]
)
def test_non_retryable_failures_are_not_retried(exc):
    clock = _Clock()
    call, calls = _failing([exc, "ok"], clock, seconds_per_call=1)

    with pytest.raises(type(exc)):
        call_with_retry(call, attempts=2, clock=clock)
    assert calls["n"] == 1


# --- rate limiter ------------------------------------------------------------------


def test_token_bucket_allows_burst_then_refills():
    clock = _Clock()
    limiter = TokenBucketLimiter(rate_per_minute=6, burst=2, clock=clock)

    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == 10  # one token per 10s at 6/min
    assert limiter.acquire("b") is None  # buckets are per client

    clock.now += 10
    assert limiter.acquire("a") is None
