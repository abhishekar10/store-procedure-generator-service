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
.venv/bin/python -m app.rag.ingest
```

A CLI command only -- there is no HTTP ingest endpoint. It runs at image
build time and as a manual job. Re-embeds both Chroma collections (`schema_chunks`, `pattern_templates`) from
`db/metadata.json` + `db/reference.db` and `app/sp_templates/`. Re-run this
after editing the schema metadata, the reference schema, or any pattern
snippet. Collections are updated in place, so repeated runs do not leave
orphaned segment directories behind. Changing the embedding model changes the
vector dimension: delete `data/vector_store/` before re-ingesting.

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
| GET    | `/health`                  | Liveness: the process is up. No dependencies                                 |
| GET    | `/ready`                   | Readiness: config present, reference DB and both collections loaded (503 if not) |
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

or `event: error` with `data: {"code": "...", "message": "...", "request_id": "..."}`
in place of `done` if generation fails partway through. `code` is one of the
stable error codes below; the raw upstream error never reaches the client.

While the model is thinking the stream sends an SSE comment line,
`: keepalive`, every `SSE_HEARTBEAT_S` (default 15s), so a load balancer
never sees the connection idle long enough to drop it. SSE clients ignore
comment lines.

## Hardening

Authentication is out of scope by design: in a real deployment the service
sits behind an ALB or API gateway that authenticates callers first. What the
service itself does:

**Request validation.** `prompt` is required, non-blank and at most
`MAX_PROMPT_CHARS` (default 8000) characters; unknown fields are rejected;
`conversation_id` is length- and charset-bounded. Bodies over
`MAX_REQUEST_BYTES` (default 64 KB) get a 413 before they are parsed. A
validation failure reports which field and why -- not the input itself.

**Typed errors.** Every error body is
`{"error": {"code", "message", "request_id", "details"?}}`, with no stack
trace or upstream payload. Codes are stable; messages may change.

| Code | Status | When |
|---|---|---|
| `invalid_request` | 422 | body failed validation |
| `bad_request` | 400 | well-formed but unusable (e.g. nothing to regenerate) |
| `not_found` | 404 | unknown route or conversation |
| `request_too_large` | 413 | body over `MAX_REQUEST_BYTES` |
| `rate_limited` | 429 | in-process limiter refused it (`Retry-After` set) |
| `not_configured` | 503 | `GEMINI_API_KEY` missing |
| `index_unavailable` | 503 | vector store not loaded |
| `upstream_rate_limited` | 503 | Gemini returned 429 |
| `upstream_timeout` | 503 | Gemini did not answer in time |
| `upstream_unavailable` | 503 | any other Gemini or transport failure |
| `internal_error` | 500 | anything else (logged with traceback server-side) |

**Timeouts and retries** (`app/rag/llm.py`). All Gemini clients -- the
classifier, generation and embeddings -- are built in one place. The
classifier (20s) and embeddings (20s) use the client library's own retry, 3
attempts. Generation (300s, above the observed 232s worst case) does not:
that retry also fires after a timeout, which would turn one 300s timeout
into several. Generation retries once, and only a 429/500/502/503 that
arrived within 30s of the attempt starting; a stream is retried only before
any text has been sent. All of these are environment variables (see
`app/config.py`).

**Thinking level** (`GENERATION_THINKING_LEVEL`, default `low`). Nearly all
generation time was the model's hidden reasoning, not this service: with the
model's own default, gemini-3.8-flash spent 4k-21k thinking tokens on a
~2k-token script and took 39-162s; at `low` it took 11-16s, and all four
golden scripts still compiled and ran against the seed data in Postgres.
`auto` restores the model's default; `medium` and `high` are also accepted.
The model alias floats, so if a future model rejects the level with a 400,
logs a `thinking_level_rejected` warning, retries at once without
it, and stays on the model default for that model until restart -- slower,
but not failing. The 300s timeout is kept because `auto`, or that fallback,
still takes minutes.

The classifier has its own setting, `CLASSIFIER_THINKING_LEVEL` (default
`low`, same values and the same fallback). Over 23 scenarios run 3 times each
-- on- and off-topic requests, follow-ups, typos, Hinglish, a prompt-injection
attempt, an off-topic pivot mid-conversation -- `auto` and `low` gave
identical answers on every run; `low` cut the median call from 3.9s to 2.4s
and the slowest from 17.4s to 5.7s. A rejected level is remembered per model
*and* level, so a model refusing generation's level does not switch off a
different level the classifier still uses. Both levels appear on the
`classification` and `generation` log lines as `thinking_level`.

**Rate limiting** (`app/ratelimit.py`). An in-process token bucket on the
two generation endpoints, per client IP: 6/minute, burst 3 by default. It is
a fallback -- each task keeps its own buckets, so N tasks allow N times the
rate. The real per-client limit belongs in front of the service (AWS WAF
rate-based rules on the ALB, or API gateway usage plans). Behind an ALB set
`TRUST_FORWARDED_FOR=true` so the client is taken from `X-Forwarded-For`.

**Concurrency and shutdown.** Model calls are blocking, so they run off the
event loop: `/generate` on FastAPI's threadpool, `/generate/stream` on a
worker thread that also stops early if the client disconnects. `/health`
stays responsive during generations. On SIGTERM uvicorn waits up to
`GRACEFUL_SHUTDOWN_S` (110s) for in-flight requests; the ECS task's
`stopTimeout` must be above that, and a generation longer than ECS allows
(120s max) is still cut off by a deploy.

## Generated-script validation

Every generation is one deployable script. The seven `-- SECTION:` markers
are SQL comments, so they sit inside a single procedure body:

| Section | Holds |
|---|---|
| `PRE_VALIDATION` | `CREATE OR REPLACE PROCEDURE ... AS $$`, `DECLARE`, `BEGIN`, then guard checks |
| `TEMP_TABLES` | statements inside the body that stage rows (`ON COMMIT DROP`) |
| `BACKUP` | statements inside the body that snapshot the rows about to change |
| `MAIN_SCRIPT` | statements inside the body that make the change |
| `POST_VALIDATION` | statements inside the body that check the result and `RAISE` on failure |
| `ROLLBACK_AUTO` | `EXCEPTION WHEN OTHERS THEN ... RAISE; END; $$;`, closing the procedure |
| `ROLLBACK_MANUAL` | a separate `rollback_*` procedure that restores from the backup |

Each generation also gets a best-effort structural check
(`app/rag/validator.py`): balanced parentheses and `$$`, all seven sections
non-empty, a procedure declared only in `PRE_VALIDATION` and
`ROLLBACK_MANUAL`, `ROLLBACK_AUTO` closing the body with an `EXCEPTION`
handler, and no `$$` inside the body sections (it would end the body early).
It is not a real SQL parser -- it catches the kinds of malformed output an
LLM occasionally produces, not genuine PL/pgSQL correctness.

The pattern templates in `app/sp_templates/` are one worked example of the
same shape (reassigning a department's employees -- deliberately none of the
golden eval prompts). `tests/test_pattern_templates.py` checks that they
assemble into a valid script. They are retrieved into every prompt as house
style, so a template that breaks the contract teaches the model to break it.

## Logging & metrics

Logs are one JSON object per line on stdout (`app/observability.py`), each
carrying the request's `request_id` -- the caller's `X-Request-ID` if
supplied, otherwise generated, and echoed back as a response header. Each
generation logs its classification (accept/reject, latency), retrieval
(tables and similarity scores, latency) and generation (latency, validator
result). Prompts are never logged at INFO: only their length and a short
hash.

Metrics are CloudWatch Embedded Metric Format lines, not a `/metrics` scrape
endpoint: each generation emits one log line with an `_aws` block, which
CloudWatch turns into metrics with no agent or extra port. Namespace
`SPGenerator`, dimensions `Endpoint` x `Outcome` (accepted / rejected /
error), metrics `Requests`, `TotalLatency`, `ClassifyLatency`,
`RetrievalLatency`, `GenerationLatency`, `SqlValid`, `RetrievedTables`.
Off AWS they are ordinary log lines.

The older SQLite telemetry store (`telemetry_events` in `data/app.db`, read
by `GET /metrics`) still records the same events; it goes when the service
becomes stateless.

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
