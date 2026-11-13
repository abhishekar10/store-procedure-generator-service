"""Assembles the RAG prompt from retrieved context and prior conversation
turns, then calls Gemini to generate or revise a stored procedure."""

import re
from collections.abc import Iterator

from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
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


class RequestRejected(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

SYSTEM_PROMPT = """You are a senior database engineer generating a production-grade \
PostgreSQL (PL/pgSQL) stored procedure.

Follow these rules:
1. Only reference tables and columns that appear in the "Schema context" below. \
Never invent a table or column name.
2. Follow the structure and idioms shown in the "Pattern context" below for each \
section -- they are examples of how this team writes each part of a procedure.
3. Write PL/pgSQL, not T-SQL or PL/SQL. Use `BEGIN ... EXCEPTION ... END` for \
error handling, not TRY/CATCH.
4. Emit your entire answer as a single SQL script, divided into exactly these \
seven sections, in this order, each starting with its own marker line and \
containing only SQL/PL-pgSQL code (no prose, no markdown fences):

-- SECTION: PRE_VALIDATION
-- SECTION: TEMP_TABLES
-- SECTION: BACKUP
-- SECTION: MAIN_SCRIPT
-- SECTION: POST_VALIDATION
-- SECTION: ROLLBACK_AUTO
-- SECTION: ROLLBACK_MANUAL

Each section has a fixed content contract:
- PRE_VALIDATION: guard checks that fail fast before any data is touched \
(target rows exist, are in the expected state, inputs are sane).
- TEMP_TABLES: CREATE TEMP TABLE statements that stage computed rows.
- BACKUP: snapshot of the rows about to be mutated.
- MAIN_SCRIPT: the one complete `CREATE OR REPLACE PROCEDURE <name>(...) \
LANGUAGE plpgsql AS $$ ... $$;` definition for this request, ending in its \
`EXCEPTION WHEN OTHERS THEN ... RAISE;` handler. This is the largest section. \
The main procedure is defined here and nowhere else.
- POST_VALIDATION: checks that the mutation left the data consistent, \
raising on failure.
- ROLLBACK_AUTO: only the `EXCEPTION WHEN OTHERS THEN ...` handler that closes \
the MAIN_SCRIPT body, restated on its own. Never a CREATE PROCEDURE.
- ROLLBACK_MANUAL: a separate `rollback_<name>` procedure that restores from \
the BACKUP snapshot after a committed run. Never a copy of the main procedure.

Worked example of the shape (abbreviated -- a real answer has full SQL in \
every section):
-- SECTION: PRE_VALIDATION
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM payroll_runs WHERE payroll_run_id = 1) \
THEN RAISE EXCEPTION 'run 1 not found'; END IF; END $$;
-- SECTION: TEMP_TABLES
CREATE TEMP TABLE tmp_calc (employee_id INTEGER, net_pay NUMERIC) ON COMMIT DROP;
-- SECTION: BACKUP
INSERT INTO payroll_entries_backup (...) SELECT ... FROM payroll_entries WHERE payroll_run_id = 1;
-- SECTION: MAIN_SCRIPT
CREATE OR REPLACE PROCEDURE process_payroll_run(p_payroll_run_id INTEGER)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE payroll_runs SET status = 'processing' WHERE payroll_run_id = p_payroll_run_id;
    -- ... the main mutation ...
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'process_payroll_run failed: %', SQLERRM;
        RAISE;
END;
$$;
-- SECTION: POST_VALIDATION
DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_entries WHERE net_pay < 0) \
THEN RAISE EXCEPTION 'negative net_pay'; END IF; END $$;
-- SECTION: ROLLBACK_AUTO
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'process_payroll_run failed: %', SQLERRM;
        RAISE;
-- SECTION: ROLLBACK_MANUAL
CREATE OR REPLACE PROCEDURE rollback_payroll_run(p_payroll_run_id INTEGER)
LANGUAGE plpgsql AS $$ BEGIN /* restore from payroll_entries_backup */ END; $$;

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


def _build_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=settings.generation_model,
        google_api_key=settings.require_gemini_api_key(),
        temperature=0.2,
    )


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
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.generation_model,
        google_api_key=settings.require_gemini_api_key(),
        temperature=0,
    )
    conversation_block = _format_classifier_conversation_block(prior_messages)
    prompt = CLASSIFIER_PROMPT.format(conversation_block=conversation_block, user_prompt=user_prompt)
    response_text = _extract_text(llm.invoke(prompt).content)

    answer_match = _ANSWER_RE.search(response_text)
    reason_match = _REASON_RE.search(response_text)

    is_valid = bool(answer_match) and answer_match.group(1).upper() == "YES"
    reason = reason_match.group(1).strip() if reason_match else _DEFAULT_REJECTION_REASON
    return is_valid, reason


def _retrieve_and_build_prompt(
    user_prompt: str, prior_messages: list[dict] | None, top_k: int | None
) -> tuple[str, dict[str, list[RetrievedChunk]]]:
    prior_messages = prior_messages or []
    prior_user_texts = [m["content"] for m in prior_messages if m["role"] == "user"]
    retrieval_query = "\n".join([*prior_user_texts, user_prompt])

    context = retrieve_context(retrieval_query, top_k=top_k)
    prompt = build_prompt(user_prompt, context, prior_messages)
    return prompt, context


def _finalize(user_prompt: str, full_script: str, context: dict[str, list[RetrievedChunk]]) -> dict:
    sql_valid, sql_issues = validate_script(full_script)
    return {
        "prompt": user_prompt,
        "full_script": full_script,
        "sections": parse_sections(full_script),
        "retrieved_context": context,
        "sql_valid": sql_valid,
        "sql_issues": sql_issues,
    }


def generate_stored_procedure(
    user_prompt: str, prior_messages: list[dict] | None = None, top_k: int | None = None
) -> dict:
    is_valid, reason = classify_request(user_prompt, prior_messages)
    if not is_valid:
        raise RequestRejected(reason)

    prompt, context = _retrieve_and_build_prompt(user_prompt, prior_messages, top_k)
    full_script = _extract_text(_build_llm().invoke(prompt).content)
    return _finalize(user_prompt, full_script, context)


def stream_stored_procedure(
    user_prompt: str, prior_messages: list[dict] | None = None, top_k: int | None = None
) -> Iterator[dict]:
    """Yields {"type": "status", "stage": ...} at each real pipeline
    transition, {"type": "chunk", "text": ...} as text arrives, and finally a
    single {"type": "done", ...} with the full finalized result -- or a
    single {"type": "rejected", "reason": ...} in place of all of the above
    if the request doesn't pass the classification gate."""
    yield {"type": "status", "stage": "checking_request"}
    is_valid, reason = classify_request(user_prompt, prior_messages)
    if not is_valid:
        yield {"type": "rejected", "reason": reason}
        return

    yield {"type": "status", "stage": "retrieving_context"}
    prompt, context = _retrieve_and_build_prompt(user_prompt, prior_messages, top_k)

    yield {"type": "status", "stage": "drafting_script"}
    full_script = ""
    for chunk in _build_llm().stream(prompt):
        text = _extract_text(chunk.content)
        if not text:
            continue
        full_script += text
        yield {"type": "chunk", "text": text}

    yield {"type": "status", "stage": "validating_script"}
    yield {"type": "done", **_finalize(user_prompt, full_script, context)}
