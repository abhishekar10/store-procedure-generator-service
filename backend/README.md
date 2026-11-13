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
    api_models.py              /v1 request + response models (the contract)
    openapi_export.py          writes docs/openapi.json
    errors.py, middleware.py, observability.py, ratelimit.py   hardening (see below)
    config.py                  paths, env vars, model names
    adapters/                  the three seams, and one implementation of each
      base.py                    LLMProvider / VectorStore / SchemaSource protocols + data types
      gemini.py                  LLMProvider: Gemini chat + embeddings, timeouts, thinking level
      chroma.py                  VectorStore: local Chroma persistent store
      sqlite_schema.py           SchemaSource: db/reference.db + db/metadata.json
    rag/
      ingest.py                  embeds schema + pattern chunks into the vector store
      retriever.py                schema similarity search + per-category pattern pull
      generator.py                 prompt assembly + model call + classification gate
      retry.py                      the generation retry policy (provider-independent)
      section_parser.py             splits `-- SECTION: X` markers into a dict
      validator.py                   best-effort structural SQL check
    sp_templates/                the authored pattern library (source docs for the pattern corpus)
  db/
    schema.sql                  reference schema DDL (9 tables)
    seed_data.sql                 sample HR/payroll data
    metadata.json                   per-table/column descriptions used for embedding
    build_db.py                      builds db/reference.db from the two files above
    reference.db                      the built SQLite database (generated, gitignored)
  data/
    vector_store/                Chroma persistent store (generated, gitignored)
  tests/                      fast, offline unit tests (no API calls) -- default `pytest` run
  evals/                       RAG quality evals (live API calls) -- run explicitly with `pytest evals/`
  pyproject.toml               pinned dependencies, pytest and ruff settings
  Dockerfile                   multi-stage image; embeds the corpora at build time
```

`db/reference.db` and `data/vector_store/` are generated
and gitignored — rebuild them with the commands below rather than editing
them directly.

## Setup

Python 3.12. Dependencies are pinned in `pyproject.toml` (the `dev` extra
adds pytest, httpx, jsonschema and ruff). With [uv](https://docs.astral.sh/uv/):

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Configuration comes from environment variables, or a `.env` file at the
repo root for local development (`cp ../.env.example ../.env`). Only
`GEMINI_API_KEY` is required; every other setting has a working default and
is listed, with what it does, in `../.env.example` and `app/config.py`.
Missing the key does not stop the service starting: `/health` answers,
`/ready` reports `not_ready`, and generation returns `not_configured`.

## Build the reference database

```bash
.venv/bin/python db/build_db.py
```

Drops and rebuilds `db/reference.db` from `db/schema.sql` + `db/seed_data.sql`.
No API key needed; safe to re-run any time.

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
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Or the whole stack in containers, from the repo root: `docker compose up --build`
(UI on :5173, API on :8000).

Interactive API docs (Swagger UI, with "Try it out" to call endpoints
directly from the browser): http://127.0.0.1:8000/api/docs
ReDoc: http://127.0.0.1:8000/api/redoc
Raw OpenAPI schema: http://127.0.0.1:8000/api/openapi.json

## Endpoints

The service is stateless: it stores no conversations. The caller sends the
conversation so far with every request and keeps the results itself, so any
task can serve any request. The demo UI keeps its history in the browser.

| Method | Path                  | Description                                                              |
|--------|-----------------------|--------------------------------------------------------------------------|
| POST   | `/v1/generate`        | `{ messages }` → full response, non-streaming                            |
| POST   | `/v1/generate/stream` | `{ messages }` → the same, as Server-Sent Events                          |
| GET    | `/v1/schema`          | Reference schema tables/columns                                          |
| GET    | `/health`             | Liveness: the process is up. No dependencies                             |
| GET    | `/ready`              | Readiness: config present, reference DB and both collections loaded (503 if not) |

`/health` and `/ready` are probes, not API, so they are not versioned.

The contract is checked in as [`docs/openapi.json`](../docs/openapi.json),
with request and response examples, and `tests/test_contract.py` pins the
service to it: the spec the app generates must match the file, and real
responses must validate against it with exactly the declared fields. After
an intended API change, regenerate it with `python -m app.openapi_export`
and commit the diff. How the service fits into a larger system -- gateway,
auth, caller-owned state, adapters -- is in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

### Request body

`messages` is the conversation so far, oldest first, ending with the new
request:

```json
{
  "messages": [
    { "role": "user", "content": "Process payroll for a given pay period" },
    { "role": "assistant", "kind": "generation", "full_script": "-- SECTION: PRE_VALIDATION\n..." },
    { "role": "user", "content": "Also skip employees who are on leave" }
  ]
}
```

| Message | Fields |
|---|---|
| user | `content`: the request, non-blank, at most `MAX_PROMPT_CHARS` (8000) |
| assistant, a script | `kind: "generation"` (the default), `full_script`: the script the service returned |
| assistant, a rejection | `kind: "rejected"`, `content`: the reason the service gave |

The last message must be from the user. Unknown fields are rejected, so send
only these -- not `sections` or `retrieved_context` from an earlier response.
**To regenerate** an answer, resend the conversation without its last
assistant message.

Schema retrieval runs against every user message in the conversation, not
just the latest, and earlier turns go into the prompt, so a follow-up reads
as a revision of the previous script rather than a fresh one.

Every request first passes a classification gate (`classify_request` in
`app/rag/generator.py`): a small, fast Gemini call that checks whether the
prompt is actually asking for a database stored procedure at all. Prompts
like "write a python program to add two numbers" get rejected before any
retrieval or generation happens, rather than producing an irrelevant script.
The gate sees the earlier user messages too, so a short follow-up like "make
it handle nulls too" is judged in context rather than in isolation -- an
earlier version only saw the latest message and incorrectly rejected
legitimate follow-ups; see `evals/test_classifier_eval.py` for the
regression cases that guard against that coming back. Keep a rejection in
the conversation you send back (as `kind: "rejected"`), so later turns know
why nothing was generated.

### Responses

`POST /v1/generate` when the request passes the gate (the stream's final
`done` event carries the same fields except `rejected` and `prompt`):

```json
{
  "rejected": false,
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

If the gate rejects the request:

```json
{ "rejected": true, "reason": "..." }
```

### Streaming protocol (`/v1/generate/stream`)

Server-Sent Events, in order:

```
event: status        (checking_request -> retrieving_context -> drafting_script -> validating_script)
data: {"stage": "..."}

event: chunk        (repeated as text arrives, between the drafting_script
data: {"text": "..."}   and validating_script status events)

event: done
data: { full_script, sections, retrieved_context, sql_valid, sql_issues }
```

or, if the classification gate rejects the request, `status: checking_request`
is followed directly by:

```
event: rejected
data: {"reason": "..."}
```

with no `retrieving_context` / `drafting_script` / `chunk` / `done` events at
all -- retrieval and generation never run for a rejected request.

`status` events mark real pipeline transitions (not a fake progress bar) --
`checking_request` for the classification gate, `retrieving_context` while
embedding the prompt and querying Chroma, `drafting_script` for the main
Gemini call (the long step), `validating_script` right before the final
structural check.

or `event: error` with `data: {"code": "...", "message": "...", "request_id": "..."}`
in place of `done` if generation fails partway through. `code` is one of the
stable error codes below; the raw upstream error never reaches the client.
The request ID is also in the `X-Request-ID` response header.

While the model is thinking the stream sends an SSE comment line,
`: keepalive`, every `SSE_HEARTBEAT_S` (default 15s), so a load balancer
never sees the connection idle long enough to drop it. SSE clients ignore
comment lines.

## Adapters

The pipeline depends on three interfaces (`app/adapters/base.py`), not on
vendor SDKs:

| Seam | Does | Implementation | Setting |
|---|---|---|---|
| `LLMProvider` | completion, streaming, embeddings | Gemini (`gemini.py`) | `LLM_PROVIDER=gemini` |
| `VectorStore` | stores chunks + vectors, nearest-neighbour search | Chroma (`chroma.py`) | `VECTOR_STORE=chroma` |
| `SchemaSource` | the reference schema generation is grounded in | SQLite (`sqlite_schema.py`) | `SCHEMA_SOURCE=sqlite` |

One implementation of each exists; the settings are where a second would be
selected (`app/adapters/__init__.py`). Swapping one means writing a class
with the same methods and adding a branch there. Two things stay outside the
interfaces: the generation retry policy (`app/rag/retry.py`, the same for any
provider), and error mapping (`app/errors.py`), which recognises google-genai,
httpx and Chroma exceptions -- a new implementation must raise those or be
added there. `SchemaSource` is also where the schema is read for both the
`/v1/schema` endpoint and ingest; it used to be read twice, with slightly
different shapes.

## Hardening

Authentication is out of scope by design: in a real deployment the service
sits behind an ALB or API gateway that authenticates callers first. What the
service itself does:

**Request validation.** Each message must match its role (see "Request
body"); user messages are non-blank and at most `MAX_PROMPT_CHARS` (default
8000) characters; unknown fields are rejected. Bodies over
`MAX_REQUEST_BYTES` (default 256 KB -- about 25 turns, since every earlier
script is resent) get a 413 before they are parsed. A validation failure
reports which field and why -- not the input itself.

**Typed errors.** Every error body is
`{"error": {"code", "message", "request_id", "details"?}}`, with no stack
trace or upstream payload. Codes are stable; messages may change.

| Code | Status | When |
|---|---|---|
| `invalid_request` | 422 | body failed validation |
| `bad_request` | 400 | well-formed but unusable |
| `not_found` | 404 | unknown route |
| `request_too_large` | 413 | body over `MAX_REQUEST_BYTES` |
| `rate_limited` | 429 | in-process limiter refused it (`Retry-After` set) |
| `not_configured` | 503 | `GEMINI_API_KEY` missing |
| `index_unavailable` | 503 | vector store not loaded |
| `upstream_rate_limited` | 503 | Gemini returned 429 |
| `upstream_timeout` | 503 | Gemini did not answer in time |
| `upstream_unavailable` | 503 | any other Gemini or transport failure |
| `internal_error` | 500 | anything else (logged with traceback server-side) |

**Timeouts and retries** (`app/adapters/gemini.py`, `app/rag/retry.py`).
All Gemini clients -- the classifier, generation and embeddings -- are built
in one place. The
classifier (20s) and embeddings (20s) use the client library's own retry, 3
attempts. Generation (200s; ~15s at thinking level `low`, up to ~160s at
`auto`) does not: that retry also fires after a timeout, which would turn
one 200s timeout into several. Generation retries once, and only a 429/500/502/503 that
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
but not failing. The 200s generation timeout is sized for `auto`, or that
fallback, which still takes minutes.

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
rate-based rules on the ALB, or API gateway usage plans). Behind proxies,
set `TRUSTED_PROXY_HOPS` to how many there are (1 behind an ALB, 2 behind
CloudFront and an ALB) so the client is taken from the right
`X-Forwarded-For` entry; entries before it are client-supplied and ignored.

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
Off AWS they are ordinary log lines. There is no `/metrics` endpoint and no
local telemetry store.

## Testing & evals

Two separate layers, on purpose -- they test different things and have very
different costs:

- **`tests/`** -- fast, deterministic, offline tests (no network, no API
  key). Covers `section_parser`, `validator`, the pattern templates, the
  thinking-level settings and fallback, the retry policy and rate limiter,
  logging and metrics, the `/v1` API end to end with the model faked (the
  request contract, history reaching the prompt, typed errors, retries, SSE
  shape and heartbeat), and the contract test against `docs/openapi.json`.
  This is what `pytest` runs by default.
- **`evals/`** -- quality evals against a small golden set, calling the real
  Gemini API (real cost). `test_classifier_eval.py` checks the
  classification gate on on-topic and off-topic prompts and on
  context-dependent follow-ups, including the exact cases that were
  misclassified before the gate was made context-aware.
  `test_retrieval_eval.py` checks schema-retrieval recall for the four
  candidate prompts in `planning/03a-reference-schema-design.md`; its
  thresholds are regression canaries, not a claim that retrieval is good
  (all nine tables are in one domain, so embeddings cluster tightly).
  `test_generation_shape_eval.py` generates all four golden prompts and
  checks the result is one procedure spanning the seven sections and passes
  the validator. Needs the vector store (`python -m app.rag.ingest`).

```bash
.venv/bin/python -m pytest -q          # 99 offline tests, ~2s
.venv/bin/python -m pytest evals/ -q   # 23 live evals, ~1 min
.venv/bin/ruff check .
```

## Models

- Generation: `models/gemini-flash-latest`
- Embeddings: `models/gemini-embedding-001`

Both are aliases/current-stable model IDs rather than pinned older versions
-- `gemini-2.0-flash` and `text-embedding-004` were tried first per the
original plan but returned 404 on this API key.
