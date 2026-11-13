"""Pins the /v1 API to the checked-in OpenAPI spec (docs/openapi.json).

Two halves, so neither the code nor the document can drift alone:
- the spec the app generates must equal the checked-in one, so a change to
  the contract shows up as a diff to docs/openapi.json in review;
- real responses must validate against the checked-in spec, and carry
  exactly the fields it declares -- pydantic's schemas allow extra fields,
  so validation alone would not notice one being added.

After an intended change: `python -m app.openapi_export`, then commit the spec.
"""

import json

import pytest
from fakes import CONTEXT, SCRIPT, FakeLLM, FakeProvider
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import app.main as main
from app.openapi_export import SPEC_PATH, render

SPEC = json.loads(SPEC_PATH.read_text())


def _validate(body: dict, schema_name: str) -> None:
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": SPEC["components"]}
    Draft202012Validator(schema).validate(body)
    declared = set(SPEC["components"]["schemas"][schema_name]["properties"])
    assert set(body) == declared, f"{schema_name}: response has {sorted(set(body) - declared)} undeclared, " \
        f"lacks {sorted(declared - set(body))}"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    main._limiter = None
    llms = {"classify": FakeLLM(["ANSWER: YES\nREASON: a stored procedure request"]), "generate": FakeLLM([SCRIPT])}
    monkeypatch.setattr("app.adapters.llm_provider", lambda: FakeProvider(llms))
    monkeypatch.setattr("app.rag.generator.retrieve_context", lambda *_args, **_kwargs: CONTEXT)
    with TestClient(main.app) as test_client:
        yield test_client, llms


def test_checked_in_spec_matches_the_app():
    assert json.loads(render()) == SPEC, "docs/openapi.json is stale: run `python -m app.openapi_export`"


def test_responses_match_the_checked_in_spec(client):
    test_client, llms = client
    body = {"messages": [{"role": "user", "content": "Process payroll for a given pay period"}]}

    generated = test_client.post("/v1/generate", json=body).json()
    _validate(generated, "GeneratedResponse")
    _validate(generated["retrieved_context"], "RetrievedContext")
    _validate(generated["retrieved_context"]["schema_chunks"][0], "RetrievedChunk")

    llms["classify"].outcomes = ["ANSWER: NO\nREASON: not a database request"]
    _validate(test_client.post("/v1/generate", json=body).json(), "RejectedResponse")

    invalid = test_client.post("/v1/generate", json={"messages": []}).json()
    _validate(invalid, "ErrorResponse")
    _validate(invalid["error"], "ErrorInfo")


def test_spec_examples_are_valid_requests(client):
    """The request examples published in the spec are accepted as-is."""
    test_client, _llms = client
    examples = SPEC["components"]["schemas"]["GenerateRequest"]["examples"]

    assert len(examples) == 2
    for example in examples:
        assert test_client.post("/v1/generate", json=example).status_code == 200
