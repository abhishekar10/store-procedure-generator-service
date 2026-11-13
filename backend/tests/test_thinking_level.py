"""GENERATION_THINKING_LEVEL / CLASSIFIER_THINKING_LEVEL, and the fallback
when a model rejects a level."""

import logging
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from pydantic import ValidationError

from app.config import get_settings
from app.rag import generator, llm


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GENERATION_RETRY_BACKOFF_S", "0")
    monkeypatch.setattr(llm, "_thinking_rejected", set())


def _rejected() -> Exception:
    """A thinking-level rejection as langchain-google-genai surfaces it: a
    400 ClientError wrapped in ChatGoogleGenerativeAIError. The message is
    the one gemini-3.8-flash returns for thinking_level=MINIMAL."""
    body = {
        "error": {
            "code": 400,
            "message": "Thinking level MINIMAL is not supported for this model. Please retry with other thinking level.",
            "status": "INVALID_ARGUMENT",
        }
    }
    try:
        raise genai_errors.ClientError(400, body)
    except genai_errors.ClientError as exc:
        try:
            raise ChatGoogleGenerativeAIError("Error calling model 'm' (INVALID_ARGUMENT)") from exc
        except ChatGoogleGenerativeAIError as wrapped:
            return wrapped


def test_both_default_to_low():
    assert llm.build_chat_model("generate").thinking_level == "low"
    assert llm.build_chat_model("classify").thinking_level == "low"


@pytest.mark.parametrize(("purpose", "var"), [("generate", "GENERATION"), ("classify", "CLASSIFIER")])
@pytest.mark.parametrize(("env", "expected"), [("auto", None), ("HIGH", "high"), (" Medium ", "medium")])
def test_thinking_level_setting(monkeypatch, purpose, var, env, expected):
    monkeypatch.setenv(f"{var}_THINKING_LEVEL", env)

    assert llm.build_chat_model(purpose).thinking_level == expected
    other = "classify" if purpose == "generate" else "generate"
    assert llm.build_chat_model(other).thinking_level == "low"  # settings are independent


@pytest.mark.parametrize("var", ["GENERATION_THINKING_LEVEL", "CLASSIFIER_THINKING_LEVEL"])
def test_unknown_thinking_level_is_a_config_error(monkeypatch, var):
    monkeypatch.setenv(var, "turbo")

    with pytest.raises(ValidationError):
        get_settings()


def test_rejected_level_falls_back_to_auto_without_using_an_attempt(caplog):
    calls = []

    def call():
        calls.append(llm.thinking_level("generate"))
        if len(calls) == 1:
            raise _rejected()
        return "ok"

    with caplog.at_level(logging.WARNING, logger="app.rag.llm"):
        assert llm.call_with_retry(call, attempts=1) == "ok"

    assert calls == ["low", None]  # retried at once, without the setting
    assert llm.build_chat_model("generate").thinking_level is None  # and it sticks
    assert [r.message for r in caplog.records] == ["thinking_level_rejected"]


def test_fallback_is_per_model(monkeypatch):
    llm.disable_thinking_if_rejected(_rejected(), "generate")
    monkeypatch.setenv("GENERATION_MODEL", "models/some-newer-flash")
    get_settings.cache_clear()

    assert llm.build_chat_model("generate").thinking_level == "low"


def test_fallback_is_per_level(monkeypatch):
    """A model refusing generation's "high" says nothing about the
    classifier's "low"; refusing "low" applies to both."""
    monkeypatch.setenv("GENERATION_THINKING_LEVEL", "high")
    llm.disable_thinking_if_rejected(_rejected(), "generate")

    assert llm.thinking_level("generate") is None
    assert llm.thinking_level("classify") == "low"


def test_classifier_falls_back_when_its_level_is_rejected(monkeypatch):
    levels = []

    def fake_build(purpose="generate"):
        assert purpose == "classify"
        levels.append(llm.thinking_level("classify"))

        def invoke(_prompt):
            if len(levels) == 1:
                raise _rejected()
            return SimpleNamespace(content="ANSWER: YES\nREASON: payroll operation")

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(generator, "_build_llm", fake_build)

    assert generator.classify_request("Process payroll") == (True, "payroll operation")
    assert levels == ["low", None]


def test_classifier_does_not_retry_other_failures(monkeypatch):
    calls = []

    def fake_build(purpose="generate"):
        def invoke(_prompt):
            calls.append(1)
            raise genai_errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid."}})

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(generator, "_build_llm", fake_build)

    with pytest.raises(genai_errors.ClientError):
        generator.classify_request("Process payroll")
    assert len(calls) == 1


def test_other_400s_are_not_mistaken_for_a_rejection():
    other = genai_errors.ClientError(400, {"error": {"code": 400, "message": "Request contains an invalid argument."}})
    calls = []

    def call():
        calls.append(1)
        raise other

    with pytest.raises(genai_errors.ClientError):
        llm.call_with_retry(call, attempts=2)
    assert len(calls) == 1
    assert llm.thinking_level("generate") == "low"


def test_no_fallback_loop_when_already_auto(monkeypatch):
    monkeypatch.setenv("GENERATION_THINKING_LEVEL", "auto")

    assert llm.disable_thinking_if_rejected(_rejected(), "generate") is False


def test_stream_falls_back_before_the_first_chunk(monkeypatch):
    levels = []

    def fake_build(purpose="generate"):
        levels.append(llm.thinking_level("generate"))

        def stream(_prompt):
            if len(levels) == 1:
                raise _rejected()
            yield SimpleNamespace(content="-- SECTION: PRE_VALIDATION\n")

        return SimpleNamespace(stream=stream)

    monkeypatch.setattr(generator, "_build_llm", fake_build)
    monkeypatch.setenv("GENERATION_ATTEMPTS", "1")

    assert list(generator._stream_generation("p")) == ["-- SECTION: PRE_VALIDATION\n"]
    assert levels == ["low", None]
