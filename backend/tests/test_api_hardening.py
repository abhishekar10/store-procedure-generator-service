"""Offline API tests for the Phase 2 hardening: validation, typed errors,
retries, rate limiting, request IDs, SSE heartbeat and error shape.

Both LLM sites are faked through `app.rag.generator._build_llm` (the
classifier and generation both build their client there), and retrieval
through `app.rag.generator.retrieve_context`, so nothing here makes a
network call or needs a vector store.
"""

import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

import app.main as main
from app.rag.retriever import RetrievedChunk

SCRIPT = (
    "-- SECTION: PRE_VALIDATION\nCREATE OR REPLACE PROCEDURE p(x INTEGER) LANGUAGE plpgsql AS $$\nBEGIN\n"
    "    IF x IS NULL THEN RAISE EXCEPTION 'x'; END IF;\n"
    "-- SECTION: TEMP_TABLES\n    CREATE TEMP TABLE t (id INTEGER) ON COMMIT DROP;\n"
    "-- SECTION: BACKUP\n    INSERT INTO b SELECT * FROM employees;\n"
    "-- SECTION: MAIN_SCRIPT\n    UPDATE employees SET job_title = 'x';\n"
    "-- SECTION: POST_VALIDATION\n    IF NOT FOUND THEN RAISE EXCEPTION 'y'; END IF;\n"
    "-- SECTION: ROLLBACK_AUTO\nEXCEPTION WHEN OTHERS THEN RAISE;\nEND;\n$$;\n"
    "-- SECTION: ROLLBACK_MANUAL\nCREATE OR REPLACE PROCEDURE rollback_p() LANGUAGE plpgsql AS $$ BEGIN NULL; END; $$;\n"
)

CONTEXT = {
    "schema_chunks": [RetrievedChunk(id="schema::employees", text="Table: employees", metadata={"table_name": "employees"}, score=0.12)],
    "pattern_chunks": [RetrievedChunk(id="pattern::backup", text="-- Pattern: BACKUP", metadata={"category": "backup"})],
}


def _overloaded() -> genai_errors.ServerError:
    return genai_errors.ServerError(503, {"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}})


def _quota() -> Exception:
    """How langchain-google-genai actually surfaces a 429: wrapped."""
    try:
        raise genai_errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})
    except genai_errors.ClientError as exc:
        try:
            raise ChatGoogleGenerativeAIError("Error calling model 'm' (RESOURCE_EXHAUSTED)") from exc
        except ChatGoogleGenerativeAIError as wrapped:
            return wrapped


class FakeLLM:
    """Plays a scripted sequence of outcomes, one per call: a string to
    return, an exception to raise, or for streams a list of chunks (an
    exception inside the list is raised mid-stream)."""

    def __init__(self, outcomes, delay_s: float = 0):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.delay_s = delay_s

    def _next(self):
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def invoke(self, _prompt):
        outcome = self._next()
        return SimpleNamespace(content=outcome if isinstance(outcome, str) else "".join(outcome))

    def stream(self, _prompt):
        outcome = self._next()
        if self.delay_s:
            threading.Event().wait(self.delay_s)
        for piece in [outcome] if isinstance(outcome, str) else outcome:
            if isinstance(piece, BaseException):
                raise piece
            yield SimpleNamespace(content=piece)


@pytest.fixture
def api(isolated_app_db, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("GENERATION_RETRY_BACKOFF_S", "0")
    main.get_settings.cache_clear()
    main._limiter = None

    llms = {"classify": FakeLLM(["ANSWER: YES\nREASON: a stored procedure request"]), "generate": FakeLLM([SCRIPT])}
    monkeypatch.setattr("app.rag.generator._build_llm", lambda purpose="generate": llms[purpose])
    monkeypatch.setattr("app.rag.generator.retrieve_context", lambda *_args, **_kwargs: CONTEXT)

    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, llms=llms)


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        name = next(line[6:].strip() for line in lines if line.startswith("event:"))
        data = json.loads("".join(line[5:].strip() for line in lines if line.startswith("data:")))
        events.append((name, data))
    return events


# --- happy path and rejection --------------------------------------------


def test_generate_happy_path(api):
    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 200
    body = response.json()
    assert body["sql_valid"] is True, body["sql_issues"]
    assert "UPDATE employees" in body["sections"]["main_script"]
    assert "timings_ms" not in body
    assert api.llms["classify"].calls == 1 and api.llms["generate"].calls == 1


def test_classifier_rejection(api):
    api.llms["classify"].outcomes = ["ANSWER: NO\nREASON: not a database request"]

    response = api.client.post("/generate", json={"prompt": "tell me a joke"})

    assert response.status_code == 200
    assert response.json()["rejected"] is True
    assert api.llms["generate"].calls == 0


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"prompt": ""}, "prompt"),
        ({"prompt": "   \n "}, "prompt"),
        ({"prompt": "x" * 8001}, "prompt"),
        ({"prompt": "ok", "unexpected": 1}, "unexpected"),
        ({"prompt": "ok", "conversation_id": "../../etc"}, "conversation_id"),
    ],
)
def test_invalid_requests_get_typed_422_without_echoing_input(api, payload, field):
    response = api.client.post("/generate", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert any(detail["field"] == field for detail in error["details"])
    assert "x" * 100 not in response.text
    assert api.llms["classify"].calls == 0


def test_stream_allows_empty_prompt_only_for_regenerate(api):
    assert api.client.post("/generate/stream", json={"prompt": ""}).status_code == 422

    response = api.client.post("/generate/stream", json={"prompt": "", "is_regenerate": True})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_oversized_body_rejected_with_413_before_parsing(api):
    response = api.client.post("/generate", json={"prompt": "x" * (70 * 1024)})

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


# --- upstream failures and retries --------------------------------------------


def test_fast_503_is_retried_once_then_succeeds(api):
    api.llms["generate"].outcomes = [_overloaded(), SCRIPT]

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 200
    assert api.llms["generate"].calls == 2


def test_persistent_503_becomes_typed_503_without_leaking_upstream_json(api):
    api.llms["generate"].outcomes = [_overloaded()]

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["request_id"] == response.headers["x-request-id"]
    assert "overloaded" not in response.text and "UNAVAILABLE" not in response.text
    assert api.llms["generate"].calls == 2  # GENERATION_ATTEMPTS default


def test_wrapped_429_is_recognised_and_mapped(api):
    api.llms["generate"].outcomes = [_quota()]

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_rate_limited"
    assert api.llms["generate"].calls == 2


def test_timeout_is_not_retried(api):
    api.llms["generate"].outcomes = [httpx.ReadTimeout("timed out")]

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.json()["error"]["code"] == "upstream_timeout"
    assert api.llms["generate"].calls == 1


def test_classifier_failure_is_typed_too(api):
    api.llms["classify"].outcomes = [_overloaded()]

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"


def test_unexpected_error_is_a_bare_500(api, monkeypatch):
    def boom(*_args, **_kwargs):
        raise ValueError("internal detail that must not leak")

    monkeypatch.setattr("app.main.generate_stored_procedure", boom)

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "internal detail" not in response.text


def test_missing_api_key_is_not_configured(api, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    main.get_settings.cache_clear()
    monkeypatch.setattr("app.rag.generator._build_llm", lambda purpose="generate": main.get_settings().require_gemini_api_key())

    response = api.client.post("/generate", json={"prompt": "Give everyone in sales a new title"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


# --- streaming ----------------------------------------------------------------


def test_stream_event_shape(api):
    api.llms["generate"].outcomes = [[SCRIPT[:200], SCRIPT[200:]]]

    response = api.client.post("/generate/stream", json={"prompt": "Give everyone in sales a new title"})

    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "start" and names[-1] == "done"
    assert [data["stage"] for name, data in events if name == "status"] == [
        "checking_request", "retrieving_context", "drafting_script", "validating_script",
    ]
    assert "".join(data["text"] for name, data in events if name == "chunk") == SCRIPT
    done = events[-1][1]
    assert done["sql_valid"] is True and "timings_ms" not in done


def test_stream_retries_failure_before_first_chunk(api):
    api.llms["generate"].outcomes = [[_overloaded()], [SCRIPT]]

    events = _sse_events(api.client.post("/generate/stream", json={"prompt": "Reassign sales"}).text)

    assert events[-1][0] == "done"
    assert api.llms["generate"].calls == 2


def test_stream_does_not_retry_after_text_was_sent(api):
    api.llms["generate"].outcomes = [[SCRIPT[:50], _overloaded()]]

    events = _sse_events(api.client.post("/generate/stream", json={"prompt": "Reassign sales"}).text)

    name, data = events[-1]
    assert name == "error"
    assert data["code"] == "upstream_unavailable" and "message" in data and data["request_id"]
    assert "overloaded" not in json.dumps(data)
    assert api.llms["generate"].calls == 1


def test_stream_overall_deadline(api, monkeypatch):
    monkeypatch.setenv("GENERATION_TIMEOUT_S", "0.05")
    main.get_settings.cache_clear()
    api.llms["generate"].outcomes = [["first", "second"]]
    api.llms["generate"].delay_s = 0.1

    events = _sse_events(api.client.post("/generate/stream", json={"prompt": "Reassign sales"}).text)

    assert events[-1] == ("error", events[-1][1]) and events[-1][1]["code"] == "upstream_timeout"


def test_stream_sends_heartbeats_while_model_thinks(api, monkeypatch):
    monkeypatch.setenv("SSE_HEARTBEAT_S", "0.02")
    main.get_settings.cache_clear()
    api.llms["generate"].delay_s = 0.2

    response = api.client.post("/generate/stream", json={"prompt": "Reassign sales"})

    assert ": keepalive" in response.text
    assert _sse_events(response.text)[-1][0] == "done"


# --- rate limiting, request IDs, routes ------------------------------------------


def test_rate_limit_returns_429_with_retry_after(api, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    main.get_settings.cache_clear()

    statuses = [api.client.post("/generate", json={"prompt": "Reassign sales"}).status_code for _ in range(3)]
    limited = api.client.post("/generate", json={"prompt": "Reassign sales"})

    assert statuses == [200, 200, 429]
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) > 0
    assert api.client.get("/health").status_code == 200  # not limited


def test_request_id_is_echoed_or_generated(api):
    echoed = api.client.get("/health", headers={"X-Request-ID": "abc-123"})
    replaced = api.client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})

    assert echoed.headers["x-request-id"] == "abc-123"
    assert replaced.headers["x-request-id"] != "bad id\nwith newline"
    assert len(replaced.headers["x-request-id"]) == 32


def test_ingest_is_no_longer_an_http_endpoint(api):
    response = api.client.post("/ingest")

    assert response.status_code in (404, 405)
    assert response.json()["error"]["code"] in ("not_found", "method_not_allowed")
