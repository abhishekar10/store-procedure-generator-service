"""Assembles the RAG prompt from retrieved context and prior conversation
turns, then calls Gemini to generate or revise a stored procedure."""

import logging
import re
import time
from collections.abc import Iterator
from typing import Literal

from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.errors import UpstreamTimeout
from app.observability import prompt_fingerprint
from app.rag.llm import (
    build_chat_model,
    call_with_retry,
    disable_thinking_if_rejected,
    should_retry_generation,
    thinking_level,
    with_thinking_fallback,
)
from app.rag.retriever import RetrievedChunk, retrieve_context
from app.rag.section_parser import parse_sections
from app.rag.validator import validate_script

CLASSIFIER_PROMPT = """You are a gatekeeper for a tool that only generates PostgreSQL \
(PL/pgSQL) stored procedures for an HR/payroll database.

Decide whether the LATEST user message below is asking for a database stored \
procedure, a SQL script, or a data/business operation that could reasonably be \
implemented as one -- either on its own, or as a follow-up refinement of an \
already-established stored-procedure conversation (e.g. "make it handle nulls \
too" or "what about weekends and holidays" are valid follow-ups if the \
conversation so far is about a stored procedure -- judge them in that context, \
not in isolation). Reject only messages that are clearly unrelated to a database \
operation even in context (e.g. "write a python function to add two numbers", \
"tell me a joke", general chit-chat).
{conversation_block}
Latest user message: "{user_prompt}"

Respond with exactly two lines, nothing else:
ANSWER: YES or NO
REASON: one short sentence explaining why
"""

_ANSWER_RE = re.compile(r"ANSWER:\s*(YES|NO)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE)

_DEFAULT_REJECTION_REASON = "This doesn't look like a request for a database stored procedure."


logger = logging.getLogger(__name__)


class RequestRejected(Exception):
    def __init__(self, reason: str, timings_ms: dict[str, float] | None = None):
        self.reason = reason
        self.timings_ms = timings_ms or {}
        super().__init__(reason)


def _ms_since(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)

SYSTEM_PROMPT = """You are a senior database engineer generating a production-grade \
PostgreSQL (PL/pgSQL) stored procedure.

Follow these rules:
1. Only reference tables and columns that appear in the "Schema context" below. \
Never invent a table or column name.
2. The "Pattern context" below is one complete worked example -- a different \
operation, split into the same seven sections -- showing how this team writes \
each part. Reuse its structure and idioms. Do not reuse its tables, columns or \
logic unless the request is that same operation.
3. Write PL/pgSQL, not T-SQL or PL/SQL. Use `BEGIN ... EXCEPTION ... END` for \
error handling, not TRY/CATCH.
4. Emit your entire answer as a single SQL script that runs top to bottom, \
divided into exactly these seven sections, in this order, each starting with \
its own marker line and containing only SQL/PL-pgSQL code (no prose, no \
markdown fences):

-- SECTION: PRE_VALIDATION
-- SECTION: TEMP_TABLES
-- SECTION: BACKUP
-- SECTION: MAIN_SCRIPT
-- SECTION: POST_VALIDATION
-- SECTION: ROLLBACK_AUTO
-- SECTION: ROLLBACK_MANUAL

The script defines ONE main procedure whose body runs from PRE_VALIDATION to \
the end of ROLLBACK_AUTO. The marker lines are SQL comments, so they sit inside \
that body. Each section has a fixed content contract:
- PRE_VALIDATION: opens the main procedure -- `CREATE OR REPLACE PROCEDURE \
<name>(<params>) LANGUAGE plpgsql AS $$`, a DECLARE block for every variable \
the body uses, `BEGIN` -- then guard checks that fail fast before any data is \
touched.
- TEMP_TABLES: statements inside the body that stage rows in temp tables \
(`ON COMMIT DROP`).
- BACKUP: statements inside the body that snapshot the rows about to be mutated.
- MAIN_SCRIPT: statements inside the body that make the change the request \
asks for. No procedure header here.
- POST_VALIDATION: statements inside the body that check the result and RAISE \
on failure.
- ROLLBACK_AUTO: closes the main procedure -- `EXCEPTION WHEN OTHERS THEN ... \
RAISE;`, then `END;` and `$$;`.
- ROLLBACK_MANUAL: a separate `CREATE OR REPLACE PROCEDURE rollback_<name>` \
that restores from the BACKUP snapshot after a committed run.

Inside the body (TEMP_TABLES through POST_VALIDATION) never write `$$`, not \
even in a comment, and never use `DO` blocks: a `$$` there ends the body early.

Skeleton of the shape:
-- SECTION: PRE_VALIDATION
CREATE OR REPLACE PROCEDURE <name>(<params>)
LANGUAGE plpgsql
AS $$
DECLARE
    <variables>
BEGIN
    <guard checks>
-- SECTION: TEMP_TABLES
    <CREATE TEMP TABLE ... ON COMMIT DROP; INSERT INTO it ...>
-- SECTION: BACKUP
    <snapshot the rows about to change>
-- SECTION: MAIN_SCRIPT
    <the change itself>
-- SECTION: POST_VALIDATION
    <checks that RAISE EXCEPTION on failure>
-- SECTION: ROLLBACK_AUTO
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE '<name> failed: %', SQLERRM;
        RAISE;
END;
$$;
-- SECTION: ROLLBACK_MANUAL
CREATE OR REPLACE PROCEDURE rollback_<name>(<params>)
LANGUAGE plpgsql
AS $$
BEGIN
    <restore from the backup>
END;
$$;

If a section genuinely doesn't apply to this request, still include its marker \
followed by a one-line comment explaining why it's empty.

5. If this is a follow-up in an ongoing conversation (a "Conversation so far" \
section is present below), treat the latest user request as a revision of the \
previous script: keep everything the user didn't ask to change, and still emit \
the complete script with all seven sections, not just a diff.
"""


def _extract_text(content: str | list) -> str:
    """ChatGoogleGenerativeAI returns `.content` as a plain string for some
    models and as a list of content blocks (text/thinking/etc.) for others --
    normalize both into a single string."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _format_schema_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(chunk.text for chunk in chunks)


def _format_pattern_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(f"### Pattern: {chunk.metadata['category']}\n{chunk.text}" for chunk in chunks)


def _format_transcript(prior_messages: list[dict]) -> str:
    lines = []
    for message in prior_messages:
        if message["role"] == "user":
            lines.append(f"User: {message['content']}")
        elif message.get("kind") == "rejected":
            lines.append(f"Assistant: (declined this request -- {message['content']})")
        else:
            lines.append(f"Assistant (previous script):\n{message['full_script']}")
    return "\n\n".join(lines)


def build_prompt(
    user_prompt: str,
    context: dict[str, list[RetrievedChunk]],
    prior_messages: list[dict] | None = None,
) -> str:
    prior_messages = prior_messages or []

    parts = [
        SYSTEM_PROMPT,
        f"Schema context (only these tables/columns exist):\n{_format_schema_context(context['schema_chunks'])}",
        f"Pattern context (house style for each section):\n{_format_pattern_context(context['pattern_chunks'])}",
    ]

    if prior_messages:
        parts.append(f"Conversation so far:\n{_format_transcript(prior_messages)}")
        parts.append(f"Latest user request:\n{user_prompt}")
    else:
        parts.append(f"User request:\n{user_prompt}")

    return "\n\n".join(parts)


def _build_llm(purpose: Literal["classify", "generate"] = "generate") -> ChatGoogleGenerativeAI:
    """Both LLM call sites (classifier and generation) build their client
    here, so timeouts, retries and test fakes cover both."""
    return build_chat_model(purpose)


def _format_classifier_conversation_block(prior_messages: list[dict] | None) -> str:
    prior_user_texts = [m["content"] for m in (prior_messages or []) if m["role"] == "user"]
    if not prior_user_texts:
        return ""
    transcript = "\n".join(f"- {text}" for text in prior_user_texts)
    return f"\nConversation so far (earlier user messages):\n{transcript}\n"


def classify_request(user_prompt: str, prior_messages: list[dict] | None = None) -> tuple[bool, str]:
    """A small, fast gate that runs before retrieval/generation: rejects
    requests that aren't actually asking for a database stored procedure
    (e.g. "write a python program to add two numbers"), rather than
    generating an irrelevant script anyway. Prior user messages are included
    so a short follow-up ("make it handle nulls too") is judged in the
    context of an already-established stored-procedure conversation, not in
    isolation."""
    conversation_block = _format_classifier_conversation_block(prior_messages)
    prompt = CLASSIFIER_PROMPT.format(conversation_block=conversation_block, user_prompt=user_prompt)
    response = with_thinking_fallback(lambda: _build_llm("classify").invoke(prompt), "classify")
    response_text = _extract_text(response.content)

    answer_match = _ANSWER_RE.search(response_text)
    reason_match = _REASON_RE.search(response_text)

    is_valid = bool(answer_match) and answer_match.group(1).upper() == "YES"
    reason = reason_match.group(1).strip() if reason_match else _DEFAULT_REJECTION_REASON
    return is_valid, reason


def _classify(user_prompt: str, prior_messages: list[dict] | None, timings: dict[str, float]) -> tuple[bool, str]:
    start = time.monotonic()
    is_valid, reason = classify_request(user_prompt, prior_messages)
    timings["classify"] = _ms_since(start)
    logger.info(
        "classification",
        extra={
            "accepted": is_valid,
            # The classifier's reason paraphrases the request, so it is kept
            # only for rejections, where it is what you need to tune the gate.
            "reason": None if is_valid else reason,
            "latency_ms": timings["classify"],
            "thinking_level": thinking_level("classify") or "auto",
            "prior_messages": len(prior_messages or []),
            **prompt_fingerprint(user_prompt),
        },
    )
    logger.debug("classification.prompt", extra={"prompt": user_prompt})
    return is_valid, reason


def _retrieve_and_build_prompt(
    user_prompt: str, prior_messages: list[dict] | None, top_k: int | None, timings: dict[str, float]
) -> tuple[str, dict[str, list[RetrievedChunk]]]:
    start = time.monotonic()
    prior_messages = prior_messages or []
    prior_user_texts = [m["content"] for m in prior_messages if m["role"] == "user"]
    retrieval_query = "\n".join([*prior_user_texts, user_prompt])

    context = retrieve_context(retrieval_query, top_k=top_k)
    prompt = build_prompt(user_prompt, context, prior_messages)
    timings["retrieval"] = _ms_since(start)
    logger.info(
        "retrieval",
        extra={
            "latency_ms": timings["retrieval"],
            "retrieved_tables": retrieved_tables(context),
            "pattern_count": len(context["pattern_chunks"]),
            "llm_prompt_chars": len(prompt),
        },
    )
    return prompt, context


def retrieved_tables(context: dict[str, list[RetrievedChunk]]) -> list[dict]:
    return [
        {"table_name": chunk.metadata.get("table_name"), "score": _round(chunk.score)}
        for chunk in context["schema_chunks"]
    ]


def _round(score: float | None) -> float | None:
    return None if score is None else round(score, 4)


def _finalize(
    user_prompt: str, full_script: str, context: dict[str, list[RetrievedChunk]], timings: dict[str, float]
) -> dict:
    sql_valid, sql_issues = validate_script(full_script)
    logger.info(
        "generation",
        extra={
            "latency_ms": timings.get("generation"),
            "thinking_level": thinking_level("generate") or "auto",
            "script_chars": len(full_script),
            "sql_valid": sql_valid,
            "sql_issues": sql_issues,
        },
    )
    return {
        "prompt": user_prompt,
        "full_script": full_script,
        "sections": parse_sections(full_script),
        "retrieved_context": context,
        "sql_valid": sql_valid,
        "sql_issues": sql_issues,
        "timings_ms": timings,
    }


def generate_stored_procedure(
    user_prompt: str, prior_messages: list[dict] | None = None, top_k: int | None = None
) -> dict:
    timings: dict[str, float] = {}
    is_valid, reason = _classify(user_prompt, prior_messages, timings)
    if not is_valid:
        raise RequestRejected(reason, timings)

    prompt, context = _retrieve_and_build_prompt(user_prompt, prior_messages, top_k, timings)

    settings = get_settings()
    start = time.monotonic()
    response = call_with_retry(
        lambda: _build_llm("generate").invoke(prompt),
        attempts=settings.generation_attempts,
    )
    timings["generation"] = _ms_since(start)
    return _finalize(user_prompt, _extract_text(response.content), context, timings)


def _stream_generation(prompt: str) -> Iterator[str]:
    """Streams generated text. A failure is retried only while nothing has
    been emitted yet (once text has reached the caller there is no clean way
    to restart), and only under the same fast-429/5xx rule as invoke.

    The client's read timeout bounds each gap between chunks; the deadline
    here bounds the whole stream, which a slow trickle could otherwise
    stretch indefinitely."""
    settings = get_settings()
    deadline = time.monotonic() + settings.generation_timeout_s

    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        emitted = False
        try:
            for chunk in _build_llm("generate").stream(prompt):
                if time.monotonic() > deadline:
                    raise UpstreamTimeout(f"generation exceeded {settings.generation_timeout_s}s")
                text = _extract_text(chunk.content)
                if text:
                    emitted = True
                    yield text
            return
        except UpstreamTimeout:
            raise
        except Exception as exc:
            if not emitted and disable_thinking_if_rejected(exc, "generate"):
                attempt -= 1  # not a failed attempt: retry at once, without the setting
                continue
            elapsed_s = time.monotonic() - started
            if emitted or not should_retry_generation(
                exc, attempt=attempt, attempts=settings.generation_attempts, elapsed_s=elapsed_s
            ):
                raise
            time.sleep(settings.generation_retry_backoff_s * attempt)


def stream_stored_procedure(
    user_prompt: str, prior_messages: list[dict] | None = None, top_k: int | None = None
) -> Iterator[dict]:
    """Yields {"type": "status", "stage": ...} at each real pipeline
    transition, {"type": "chunk", "text": ...} as text arrives, and finally a
    single {"type": "done", ...} with the full finalized result -- or a
    single {"type": "rejected", "reason": ...} in place of all of the above
    if the request doesn't pass the classification gate. Both terminal
    events carry `timings_ms` per stage."""
    timings: dict[str, float] = {}

    yield {"type": "status", "stage": "checking_request"}
    is_valid, reason = _classify(user_prompt, prior_messages, timings)
    if not is_valid:
        yield {"type": "rejected", "reason": reason, "timings_ms": timings}
        return

    yield {"type": "status", "stage": "retrieving_context"}
    prompt, context = _retrieve_and_build_prompt(user_prompt, prior_messages, top_k, timings)

    yield {"type": "status", "stage": "drafting_script"}
    start = time.monotonic()
    full_script = ""
    for text in _stream_generation(prompt):
        full_script += text
        yield {"type": "chunk", "text": text}
    timings["generation"] = _ms_since(start)

    yield {"type": "status", "stage": "validating_script"}
    yield {"type": "done", **_finalize(user_prompt, full_script, context, timings)}
