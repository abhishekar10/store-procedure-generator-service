"""Offline API tests for the stateless /v1 API: the request contract,
validation, typed errors, retries, rate limiting, request IDs, SSE heartbeat
and error shape.

The LLM provider is faked through `app.adapters.llm_provider` (classifier
and generation both get their provider there), and retrieval through
`app.rag.generator.retrieve_context`, so nothing here makes a network call or
needs a vector store.
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
        self.prompts: list[str] = []

    def _next(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def complete(self, prompt):
        outcome = self._next(prompt)
        return outcome if isinstance(outcome, str) else "".join(outcome)

    def stream(self, prompt):
        outcome = self._next(prompt)
        if self.delay_s:
            threading.Event().wait(self.delay_s)
        for piece in [outcome] if isinstance(outcome, str) else outcome:
            if isinstance(piece, BaseException):
                raise piece
            yield piece


class FakeProvider:
    """An LLMProvider that routes each purpose to its own FakeLLM."""

    def __init__(self, llms):
        self.llms = llms

    def complete(self, prompt, purpose):
        return self.llms[purpose].complete(prompt)

    def stream(self, prompt, purpose):
        yield from self.llms[purpose].stream(prompt)

    def log_fields(self, purpose):
        return {"model": "fake"}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("GENERATION_RETRY_BACKOFF_S", "0")
    main.get_settings.cache_clear()
    main._limiter = None

    llms = {"classify": FakeLLM(["ANSWER: YES\nREASON: a stored procedure request"]), "generate": FakeLLM([SCRIPT])}
    monkeypatch.setattr("app.adapters.llm_provider", lambda: FakeProvider(llms))
    monkeypatch.setattr("app.rag.generator.retrieve_context", lambda *_args, **_kwargs: CONTEXT)

    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, llms=llms)


def ask(text: str, *prior: dict) -> dict:
    """A request body: the conversation so far, ending with `text`."""
    return {"messages": [*prior, {"role": "user", "content": text}]}


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
    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 200
    body = response.json()
    assert body["rejected"] is False
    assert body["sql_valid"] is True, body["sql_issues"]
    assert "conversation_id" not in body and "message_id" not in body
    assert "UPDATE employees" in body["sections"]["main_script"]
    assert "timings_ms" not in body
    assert api.llms["classify"].calls == 1 and api.llms["generate"].calls == 1


def test_classifier_rejection(api):
    api.llms["classify"].outcomes = ["ANSWER: NO\nREASON: not a database request"]

    response = api.client.post("/v1/generate", json=ask("tell me a joke"))

    assert response.status_code == 200
    assert response.json() == {"rejected": True, "reason": "not a database request"}
    assert api.llms["generate"].calls == 0


# --- validation -------------------------------------------------------------


PREVIOUS_SCRIPT = {"role": "assistant", "kind": "generation", "full_script": "-- SECTION: PRE_VALIDATION\nprevious"}


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"messages": []}, "messages"),
        (ask(""), "messages.0"),
        (ask("   \n "), "messages.0"),
        (ask("x" * 8001), "messages.0"),
        ({"messages": [{"role": "user", "content": "Process payroll"}, PREVIOUS_SCRIPT]}, "messages"),
        ({"messages": [{"role": "assistant", "kind": "generation"}, {"role": "user", "content": "ok"}]}, "messages.0"),
        ({"messages": [{"role": "assistant", "kind": "rejected"}, {"role": "user", "content": "ok"}]}, "messages.0"),
        ({"messages": [{"role": "user", "content": "ok", "full_script": "x"}]}, "messages.0"),
        ({"messages": [{"role": "system", "content": "ok"}]}, "messages.0.role"),
        ({"messages": [{"role": "user", "content": "ok", "sections": {}}]}, "messages.0.sections"),
        ({**ask("ok"), "unexpected": 1}, "unexpected"),
        # the pre-/v1 body shape is refused, not half-understood
        ({"prompt": "ok", "conversation_id": "abc"}, "messages"),
    ],
)
def test_invalid_requests_get_typed_422_without_echoing_input(api, payload, field):
    response = api.client.post("/v1/generate", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert any(detail["field"] == field for detail in error["details"]), error["details"]
    assert "x" * 100 not in response.text
    assert api.llms["classify"].calls == 0


def test_prior_conversation_reaches_classifier_and_generator(api):
    rejected = {"role": "assistant", "kind": "rejected", "content": "not a database request"}
    body = ask("Also skip employees who are on leave", {"role": "user", "content": "Process payroll"}, PREVIOUS_SCRIPT,
               {"role": "user", "content": "tell me a joke"}, rejected)

    assert api.client.post("/v1/generate", json=body).status_code == 200

    classifier_prompt, generation_prompt = api.llms["classify"].prompts[0], api.llms["generate"].prompts[0]
    assert "Process payroll" in classifier_prompt and "skip employees who are on leave" in classifier_prompt
    assert "-- SECTION: PRE_VALIDATION\nprevious" in generation_prompt  # the earlier script, verbatim
    assert "declined this request -- not a database request" in generation_prompt


def test_assistant_kind_defaults_to_generation(api):
    body = ask("Make it handle nulls", {"role": "user", "content": "Process payroll"},
               {"role": "assistant", "full_script": "-- SECTION: PRE_VALIDATION\nprevious"})

    assert api.client.post("/v1/generate", json=body).status_code == 200
    assert "Assistant (previous script)" in api.llms["generate"].prompts[0]


def test_stream_validates_the_same_way(api):
    response = api.client.post("/v1/generate/stream", json={"messages": [PREVIOUS_SCRIPT]})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_oversized_body_rejected_with_413_before_parsing(api):
    response = api.client.post("/v1/generate", json=ask("x" * (300 * 1024)))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_a_long_conversation_fits_under_the_body_cap(api):
    """20 turns with ~8 KB scripts -- the size real conversations reach."""
    turn = [{"role": "user", "content": "Revise it"}, {**PREVIOUS_SCRIPT, "full_script": "x" * 8000}]

    assert api.client.post("/v1/generate", json=ask("Once more", *(turn * 10))).status_code == 200


# --- upstream failures and retries --------------------------------------------


def test_fast_503_is_retried_once_then_succeeds(api):
    api.llms["generate"].outcomes = [_overloaded(), SCRIPT]

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 200
    assert api.llms["generate"].calls == 2


def test_persistent_503_becomes_typed_503_without_leaking_upstream_json(api):
    api.llms["generate"].outcomes = [_overloaded()]

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["request_id"] == response.headers["x-request-id"]
    assert "overloaded" not in response.text and "UNAVAILABLE" not in response.text
    assert api.llms["generate"].calls == 2  # GENERATION_ATTEMPTS default


def test_wrapped_429_is_recognised_and_mapped(api):
    api.llms["generate"].outcomes = [_quota()]

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_rate_limited"
    assert api.llms["generate"].calls == 2


def test_timeout_is_not_retried(api):
    api.llms["generate"].outcomes = [httpx.ReadTimeout("timed out")]

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.json()["error"]["code"] == "upstream_timeout"
    assert api.llms["generate"].calls == 1


def test_classifier_failure_is_typed_too(api):
    api.llms["classify"].outcomes = [_overloaded()]

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"


def test_unexpected_error_is_a_bare_500(api, monkeypatch):
    def boom(*_args, **_kwargs):
        raise ValueError("internal detail that must not leak")

    monkeypatch.setattr("app.main.generate_stored_procedure", boom)

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "internal detail" not in response.text


def test_missing_api_key_is_not_configured(api, monkeypatch):
    """The real Gemini provider, which refuses before any network call."""
    from app.adapters.gemini import GeminiProvider

    monkeypatch.setenv("GEMINI_API_KEY", "")
    main.get_settings.cache_clear()
    monkeypatch.setattr("app.adapters.llm_provider", GeminiProvider)

    response = api.client.post("/v1/generate", json=ask("Give everyone in sales a new title"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


# --- streaming ----------------------------------------------------------------


def test_stream_event_shape(api):
    api.llms["generate"].outcomes = [[SCRIPT[:200], SCRIPT[200:]]]

    response = api.client.post("/v1/generate/stream", json=ask("Give everyone in sales a new title"))

    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "status" and names[-1] == "done"  # no "start": there is no conversation id to announce
    assert [data["stage"] for name, data in events if name == "status"] == [
        "checking_request", "retrieving_context", "drafting_script", "validating_script",
    ]
    assert "".join(data["text"] for name, data in events if name == "chunk") == SCRIPT
    done = events[-1][1]
    assert done["sql_valid"] is True and "timings_ms" not in done
    assert set(done) == {"full_script", "sections", "retrieved_context", "sql_valid", "sql_issues"}


def test_stream_rejection_event(api):
    api.llms["classify"].outcomes = ["ANSWER: NO\nREASON: not a database request"]

    events = _sse_events(api.client.post("/v1/generate/stream", json=ask("tell me a joke")).text)

    assert events[-1] == ("rejected", {"reason": "not a database request"})
    assert api.llms["generate"].calls == 0


def test_stream_retries_failure_before_first_chunk(api):
    api.llms["generate"].outcomes = [[_overloaded()], [SCRIPT]]

    events = _sse_events(api.client.post("/v1/generate/stream", json=ask("Reassign sales")).text)

    assert events[-1][0] == "done"
    assert api.llms["generate"].calls == 2


def test_stream_does_not_retry_after_text_was_sent(api):
    api.llms["generate"].outcomes = [[SCRIPT[:50], _overloaded()]]

    events = _sse_events(api.client.post("/v1/generate/stream", json=ask("Reassign sales")).text)

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

    events = _sse_events(api.client.post("/v1/generate/stream", json=ask("Reassign sales")).text)

    assert events[-1] == ("error", events[-1][1]) and events[-1][1]["code"] == "upstream_timeout"


def test_stream_sends_heartbeats_while_model_thinks(api, monkeypatch):
    monkeypatch.setenv("SSE_HEARTBEAT_S", "0.02")
    main.get_settings.cache_clear()
    api.llms["generate"].delay_s = 0.2

    response = api.client.post("/v1/generate/stream", json=ask("Reassign sales"))

    assert ": keepalive" in response.text
    assert _sse_events(response.text)[-1][0] == "done"


# --- rate limiting, request IDs, routes ------------------------------------------


def test_rate_limit_returns_429_with_retry_after(api, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    main.get_settings.cache_clear()

    statuses = [api.client.post("/v1/generate", json=ask("Reassign sales")).status_code for _ in range(3)]
    limited = api.client.post("/v1/generate", json=ask("Reassign sales"))

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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/ingest"),
        ("post", "/generate"),
        ("post", "/generate/stream"),
        ("get", "/conversations"),
        ("get", "/conversations/abc"),
        ("delete", "/conversations/abc"),
        ("get", "/metrics"),
        ("get", "/schema"),
    ],
)
def test_removed_routes_are_gone(api, method, path):
    response = getattr(api.client, method)(path)

    assert response.status_code in (404, 405)
    assert response.json()["error"]["code"] in ("not_found", "method_not_allowed")
