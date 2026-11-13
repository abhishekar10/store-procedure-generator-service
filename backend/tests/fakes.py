"""Test doubles shared by the API and contract tests: a scripted LLM, an
LLMProvider over it, and a canned script and retrieval context."""

import threading

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
