"""GENERATION_THINKING_LEVEL / CLASSIFIER_THINKING_LEVEL, and the Gemini
provider's fallback when a model rejects a level."""

import logging
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from pydantic import ValidationError

from app.adapters import gemini
from app.adapters.gemini import GeminiProvider
from app.config import get_settings
from app.rag import generator


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GENERATION_RETRY_BACKOFF_S", "0")
    monkeypatch.setattr(gemini, "_thinking_rejected", set())


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


@pytest.fixture
def chat_models(monkeypatch):
    """Replaces the LangChain chat model with a fake that refuses the first
    call that carries a thinking level, and records the level of each call."""
    levels = []

    def fake_build(purpose):
        level = gemini.thinking_level(purpose)
        levels.append(level)

        def outcome():
            if len(levels) == 1 and level is not None:
                raise _rejected()

        def invoke(_prompt):
            outcome()
            return SimpleNamespace(content="ANSWER: YES\nREASON: payroll operation")

        def stream(_prompt):
            outcome()
            yield SimpleNamespace(content="-- SECTION: PRE_VALIDATION\n")

        return SimpleNamespace(invoke=invoke, stream=stream)

    monkeypatch.setattr(gemini, "build_chat_model", fake_build)
    return levels


# --- settings -----------------------------------------------------------------


def test_both_default_to_low():
    assert gemini.build_chat_model("generate").thinking_level == "low"
    assert gemini.build_chat_model("classify").thinking_level == "low"


@pytest.mark.parametrize(("purpose", "var"), [("generate", "GENERATION"), ("classify", "CLASSIFIER")])
@pytest.mark.parametrize(("env", "expected"), [("auto", None), ("HIGH", "high"), (" Medium ", "medium")])
def test_thinking_level_setting(monkeypatch, purpose, var, env, expected):
    monkeypatch.setenv(f"{var}_THINKING_LEVEL", env)

    assert gemini.build_chat_model(purpose).thinking_level == expected
    other = "classify" if purpose == "generate" else "generate"
    assert gemini.build_chat_model(other).thinking_level == "low"  # settings are independent


@pytest.mark.parametrize("var", ["GENERATION_THINKING_LEVEL", "CLASSIFIER_THINKING_LEVEL"])
def test_unknown_thinking_level_is_a_config_error(monkeypatch, var):
    monkeypatch.setenv(var, "turbo")

    with pytest.raises(ValidationError):
        get_settings()


def test_level_is_reported_for_the_log_lines(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_THINKING_LEVEL", "auto")

    assert GeminiProvider().log_fields("generate")["thinking_level"] == "low"
    assert GeminiProvider().log_fields("classify")["thinking_level"] == "auto"


# --- fallback -------------------------------------------------------------------


def test_complete_falls_back_to_auto(chat_models, caplog):
    with caplog.at_level(logging.WARNING, logger="app.adapters.gemini"):
        assert GeminiProvider().complete("p", "generate").startswith("ANSWER: YES")

    assert chat_models == ["low", None]  # retried at once, without the setting
    assert gemini.thinking_level("generate") is None  # and it sticks
    assert [r.message for r in caplog.records] == ["thinking_level_rejected"]


def test_stream_falls_back_before_the_first_chunk(chat_models):
    assert list(GeminiProvider().stream("p", "generate")) == ["-- SECTION: PRE_VALIDATION\n"]
    assert chat_models == ["low", None]


def test_classifier_falls_back_through_the_pipeline(chat_models):
    assert generator.classify_request("Process payroll") == (True, "payroll operation")
    assert chat_models == ["low", None]


def test_fallback_does_not_use_a_generation_attempt(chat_models, monkeypatch):
    """With one attempt allowed, the retry after a refused level must still
    happen -- it is not a failed attempt."""
    monkeypatch.setenv("GENERATION_ATTEMPTS", "1")

    assert list(generator._stream_generation("p")) == ["-- SECTION: PRE_VALIDATION\n"]


def test_fallback_is_per_model(monkeypatch):
    gemini.disable_thinking_if_rejected(_rejected(), "generate")
    monkeypatch.setenv("GENERATION_MODEL", "models/some-newer-flash")
    get_settings.cache_clear()

    assert gemini.build_chat_model("generate").thinking_level == "low"


def test_fallback_is_per_level(monkeypatch):
    """A model refusing generation's "high" says nothing about the
    classifier's "low"; refusing "low" applies to both."""
    monkeypatch.setenv("GENERATION_THINKING_LEVEL", "high")
    gemini.disable_thinking_if_rejected(_rejected(), "generate")

    assert gemini.thinking_level("generate") is None
    assert gemini.thinking_level("classify") == "low"


def test_other_400s_are_not_mistaken_for_a_rejection(monkeypatch):
    calls = []

    def fake_build(purpose):
        def invoke(_prompt):
            calls.append(1)
            raise genai_errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid."}})

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(gemini, "build_chat_model", fake_build)

    with pytest.raises(genai_errors.ClientError):
        GeminiProvider().complete("p", "classify")
    assert len(calls) == 1
    assert gemini.thinking_level("classify") == "low"


def test_no_fallback_loop_when_already_auto(monkeypatch):
    monkeypatch.setenv("GENERATION_THINKING_LEVEL", "auto")

    assert gemini.disable_thinking_if_rejected(_rejected(), "generate") is False


def test_a_second_refusal_is_raised_not_retried_forever(monkeypatch):
    calls = []

    def fake_build(purpose):
        def stream(_prompt):
            calls.append(gemini.thinking_level(purpose))
            raise _rejected()
            yield  # pragma: no cover -- makes this a generator

        return SimpleNamespace(stream=stream)

    monkeypatch.setattr(gemini, "build_chat_model", fake_build)

    with pytest.raises(ChatGoogleGenerativeAIError):
        list(GeminiProvider().stream("p", "generate"))
    assert calls == ["low", None]
