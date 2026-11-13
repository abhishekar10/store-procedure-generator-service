# Backend

FastAPI service that turns a plain-English request into a PL/pgSQL stored
procedure, grounded by retrieval-augmented generation over two sources:

- **Schema corpus** — the HR/payroll reference schema (`db/`), so generation
  only references tables/columns that actually exist.
- **Pattern corpus** — a small authored library (`app/sp_templates/`) of
  best-practice snippets for pre-validation, temp tables, backup,
  post-validation, and auto/manual rollback.

Both are embedded into a local Chroma vector store and retrieved together at
generation time.

## Layout

```
backend/
  app/
    main.py                    FastAPI app and routes
    config.py                  paths, env vars, model names
    rag/
      ingest.py                  embeds schema + pattern chunks into Chroma
      retriever.py                schema similarity search + per-category pattern pull
      generator.py                 prompt assembly + Gemini call + classification gate
      section_parser.py             splits `-- SECTION: X` markers into a dict
      validator.py                   best-effort structural SQL check
    db/
      reference_schema.py         read-only queries against db/reference.db
      conversation_store.py        chat conversations + messages (data/app.db)
      telemetry_store.py            per-request metrics (data/app.db)
    sp_templates/                the authored pattern library (source docs for the pattern corpus)
  db/
    schema.sql                  reference schema DDL (9 tables)
    seed_data.sql                 sample HR/payroll data
    metadata.json                   per-table/column descriptions used for embedding
    build_db.py                      builds db/reference.db from the two files above
    reference.db                      the built SQLite database (generated, gitignored)
  data/
    app.db                     conversations + messages + telemetry (generated, gitignored)
    vector_store/                Chroma persistent store (generated, gitignored)
  tests/                      fast, offline unit tests (no API calls) -- default `pytest` run
  evals/                       RAG quality evals (live API calls) -- run explicitly with `pytest evals/`
  pytest.ini
  requirements.txt
```

`db/reference.db`, `data/app.db`, and `data/vector_store/` are all generated
and gitignored — rebuild them with the commands below rather than editing
them directly.

## Setup

Dependencies are split across two files:

- `rag/requirements.txt` (project root, one level up from `05 - RAG
  Project/`) — shared across all RAG exercises: `chromadb`,
  `langchain-google-genai`, `python-dotenv`, etc.
- `backend/requirements.txt` — backend-only additions: `fastapi`, `uvicorn`.

```bash
cd rag
source .venv/bin/activate        # shared venv for all rag/ projects
uv pip install --python .venv/bin/python3 -r "05 - RAG Project/backend/requirements.txt"
```

`GEMINI_API_KEY` is read from `rag/.env` (shared across all rag/ projects,
not duplicated per-project).

## Build the reference database

```bash
cd "05 - RAG Project/backend/db"
python3 build_db.py
```

Drops and rebuilds `reference.db` from `schema.sql` + `seed_data.sql`. Safe
to re-run any time.

## Embed the RAG corpus

```bash
cd "05 - RAG Project/backend"
python3 -m app.rag.ingest
```

Re-embeds both Chroma collections (`schema_chunks`, `pattern_templates`) from
`db/metadata.json` + `db/reference.db` and `app/sp_templates/`. Re-run this
after editing the schema metadata, the reference schema, or any pattern
snippet.

## Run the API

```bash
cd "05 - RAG Project/backend"
python3 -m uvicorn app.main:app --reload --port 8000
```

Interactive API docs (Swagger UI, with "Try it out" to call endpoints
directly from the browser): http://127.0.0.1:8000/api/docs
ReDoc: http://127.0.0.1:8000/api/redoc
Raw OpenAPI schema: http://127.0.0.1:8000/api/openapi.json

## Endpoints

| Method | Path                       | Description                                                         |
|--------|----------------------------|-----------------------------------------------------------------------|
| POST   | `/generate`                | `{ prompt, conversation_id? }` → full response, non-streaming          |
| POST   | `/generate/stream`         | `{ prompt, conversation_id?, is_regenerate? }` → same, as SSE            |
| GET    | `/conversations`           | List conversations (id, title, created_at, message_count)                 |
| GET    | `/conversations/{id}`      | Full message history for one conversation (404 if missing)                 |
| DELETE | `/conversations/{id}`      | Delete a conversation and its messages (404 if missing)                     |
| GET    | `/schema`                  | Reference schema tables/columns                                             |
| POST   | `/ingest`                  | Re-embeds the schema + pattern corpus into Chroma                            |
| GET    | `/metrics`                 | Aggregate telemetry: acceptance rate, latency per stage, output validity      |

A `conversation_id` of `null` starts a new conversation; the response/stream
returns the generated id, which the frontend then reuses for follow-up
messages in the same thread -- each `/generate` call resolves schema
retrieval against the whole conversation so far (not just the latest
message) and includes prior turns in the prompt, so follow-ups read as
revisions of the previous script rather than one-shot regenerations.
`is_regenerate: true` re-runs the last user message without adding a
duplicate user turn, appending a fresh assistant message instead.

Every request first passes a classification gate (`classify_request` in
`app/rag/generator.py`): a small, fast Gemini call that checks whether the
prompt is actually asking for a database stored procedure at all. Prompts
like "write a python program to add two numbers" get rejected before any
retrieval or generation happens, rather than producing an irrelevant script.
The gate is passed the prior conversation's user messages (not just the
latest one), so a short follow-up like "make it handle nulls too" is judged
in context rather than in isolation -- an earlier version only saw the
latest message and incorrectly rejected legitimate follow-ups; see
`evals/test_classifier_eval.py` for the regression cases that guard against
that coming back. A rejected request still gets persisted (as an assistant
message with `kind: "rejected"`, `content` holding the reason) so the
conversation history shows why nothing was generated.

`POST /generate` response shape when the request passes the gate
(`/generate/stream`'s final `done` SSE event carries the same fields, plus
`conversation_id`):

```json
{
  "conversation_id": "...",
  "message_id": "...",
  "prompt": "...",
  "full_script": "...",
  "sections": {
    "pre_validation": "...", "temp_tables": "...", "backup": "...",
    "main_script": "...", "post_validation": "...",
    "rollback_auto": "...", "rollback_manual": "..."
  },
  "retrieved_context": {
    "schema_chunks": [{ "id": "...", "text": "...", "metadata": { "table_name": "..." }, "score": 0.31 }],
    "pattern_chunks": [{ "id": "...", "text": "...", "metadata": { "category": "..." }, "score": null }]
  },
  "sql_valid": true,
  "sql_issues": []
}
```

If the gate rejects the request, `POST /generate` instead returns:

```json
{ "conversation_id": "...", "message_id": "...", "rejected": true, "reason": "..." }
```

### Streaming protocol (`/generate/stream`)

Server-Sent Events, in order:

```
event: start
data: {"conversation_id": "..."}

event: status        (checking_request -> retrieving_context -> drafting_script -> validating_script)
data: {"stage": "..."}

event: chunk        (repeated as text arrives, between the drafting_script
data: {"text": "..."}   and validating_script status events)

event: done
data: { ...full response shape above... }
```

or, if the classification gate rejects the request, `status: checking_request`
is followed directly by:

```
event: rejected
data: {"conversation_id": "...", "message_id": "...", "reason": "..."}
```

with no `retrieving_context` / `drafting_script` / `chunk` / `done` events at
all -- retrieval and generation never run for a rejected request.

`status` events mark real pipeline transitions (not a fake progress bar) --
`checking_request` for the classification gate, `retrieving_context` while
embedding the prompt and querying Chroma, `drafting_script` for the main
Gemini call (this is the long step; Gemini's reasoning time before the first
token can be 30-60s), `validating_script` right before the final structural
check.

or `event: error` with `data: {"message": "..."}` in place of `done` if
generation fails partway through.

## Validation

Every generation gets a best-effort structural check (`app/rag/validator.py`):
balanced parentheses, balanced `$$` dollar-quoted blocks, all seven
sections present and non-empty, and the main procedure in the right place:
`MAIN_SCRIPT` must declare a procedure, no section other than `MAIN_SCRIPT`
and `ROLLBACK_MANUAL` may declare one, and `MAIN_SCRIPT` must not be under a
quarter the size of the largest other section. It is not a real SQL parser --
it catches the kinds of malformed output an LLM occasionally produces, not
genuine PL/pgSQL correctness.

Section contract: `MAIN_SCRIPT` holds the one `CREATE OR REPLACE PROCEDURE`
for the request, ending in its exception handler; `ROLLBACK_AUTO` restates
only that `EXCEPTION WHEN OTHERS` handler; `ROLLBACK_MANUAL` is a separate
`rollback_*` procedure that restores from the backup. The pattern templates
in `app/sp_templates/` follow the same contract, and
`tests/test_pattern_templates.py` enforces it -- they are retrieved into
every prompt as house style, so a template that breaks the contract teaches
the model to break it.

## Telemetry & metrics

Every `/generate` and `/generate/stream` call logs one row to
`telemetry_events` in `data/app.db` (`app/db/telemetry_store.py`) --
whether it was accepted or rejected, latency broken down per pipeline stage
(classification / retrieval / generation), which tables got retrieved, and
whether the output passed the structural validator. `GET /metrics` returns
the aggregate: acceptance/rejection rate, average latency per stage,
`sql_valid` rate, and the most-frequently-retrieved tables.

This is intentionally basic -- no tracing, no percentiles, no alerting, no
external tool (no OpenTelemetry/Prometheus/Datadog). It answers "is this
working, and roughly how well" from inside the codebase, which is enough to
know the fundamentals of what production monitoring for a RAG system needs
to capture, without pulling in infrastructure this project doesn't need yet.

```bash
curl http://127.0.0.1:8000/metrics
```

## Testing & evals

Two separate layers, on purpose -- they test different things and have very
different costs:

- **`tests/`** -- fast, deterministic, offline unit tests (no network, no API
  key required). Covers `section_parser`, `validator`, `conversation_store`
  (including a regression test for a foreign-key-ordering bug that
  previously made `DELETE /conversations/{id}` 500), and `telemetry_store`'s
  aggregation math. This is what `pytest` runs by default.
- **`evals/`** -- RAG *quality* evals against a small golden set, calling the
  real Gemini API (real cost, ~30s total). `test_classifier_eval.py` checks
  the classification gate's accuracy on a labeled set of on-topic/off-topic
  prompts and context-dependent follow-ups (including the exact cases that
  were misclassified before the gate was made context-aware).
  `test_retrieval_eval.py` checks schema-retrieval recall against the four
  candidate prompts documented in `planning/03a-reference-schema-design.md`,
  with thresholds set as regression canaries, not a claim that retrieval is
  perfect -- see the retrieval precision limitation noted in
  `06 - Interview Questions/00-review-findings.md`.

```bash
cd "05 - RAG Project/backend"
python3 -m pytest              # fast unit tests only (~0.05s)
python3 -m pytest evals/       # live-API evals (~30s, costs real tokens)
```

## Models

- Generation: `models/gemini-flash-latest`
- Embeddings: `models/gemini-embedding-001`

Both are aliases/current-stable model IDs rather than pinned older versions
-- `gemini-2.0-flash` and `text-embedding-004` were tried first per the
original plan but returned 404 on this API key.
