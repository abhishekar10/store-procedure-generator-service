# Migration status and next steps

**Read this first.** The original spec, `RAG_SERVICE_INSTRUCTIONS.md`, is kept locally
by the owner and is no longer in the repository (gitignored since 2026-11-15); it is still
authoritative for scope and working rules. This file records what has actually been
built and verified, the measurements that constrain the remaining work, and what to do
next. Everything here was verified by running it — nothing is aspirational. Where a
claim is a measurement, the number is given.

Last updated after Phase 2. Repo: `github.com/abhishekar10/store-procedure-generator-service`

---

## 1. What this project is

A demonstration of an enterprise-style microservice that generates PL/pgSQL stored
procedures from plain-English requests, using RAG over two corpora (an HR/payroll
reference schema, and a library of SQL pattern templates). The React UI is a demo
client only.

**Scope decisions already made — do not revisit:**

- **Auth is out of scope.** In a real deployment the service sits behind an API gateway
  or ALB. Document where auth would sit; do not build it.
- Hardening is at microservice level only: validation, rate limiting, timeouts, retries,
  safe config.
- The service must become stateless. The caller owns conversation history.
- This is shown to employers. **Never claim anything that does not exist.** Label
  anything unbuilt as "planned".

---

## 2. Environment

This repo was extracted out of a notes monorepo. It no longer depends on any parent
directory.

| Thing | Where |
|---|---|
| Repo root | `~/Desktop/store-procedure-generator-service` |
| Python venv | `backend/.venv` (Python 3.12.13) |
| Python interpreter | uv-managed, at `~/.local/share/uv/python` — **not** snap-scoped |
| Secrets | `.env` at repo root, gitignored, mode 600. Template: `.env.example` |
| Terraform 1.17.2 | `~/.local/bin/terraform` |
| AWS CLI 2.40.2 | `~/.local/bin/aws` |

### Why the interpreter location matters

The original shared venv was built against a VS Code **snap revision** path
(`~/snap/code/253/...`). The snap updated, that path vanished, and the venv broke
completely — nothing could run. The new venv deliberately uses a uv-managed interpreter
under `~/.local/share/uv/python`, which survives snap updates. If you recreate the venv,
keep it off the snap path.

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

### Everyday commands

```bash
cd backend

.venv/bin/python -m pytest -q          # 99 offline tests, ~2s, no API key needed
.venv/bin/python -m pytest evals/ -q   # 23 live-API evals, ~1 min, costs real tokens
.venv/bin/python -m app.openapi_export # regenerate docs/openapi.json after an API change
.venv/bin/ruff check .                 # lint
.venv/bin/ruff check . --fix

.venv/bin/python db/build_db.py        # rebuild reference.db (no key needed)
.venv/bin/python -m app.rag.ingest     # re-embed both collections (needs key)

.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Container:

```bash
export GEMINI_API_KEY=$(grep '^GEMINI_API_KEY=' .env | cut -d= -f2-)

DOCKER_BUILDKIT=1 docker build \
    --secret id=gemini_key,env=GEMINI_API_KEY \
    -t sp-generator:dev backend/

docker compose up --build -d    # UI :5173, API :8000
docker compose down
```

---

## 3. What is done

### Phase 0 — extraction (complete)

Extracted from `Notes/rag/05 - RAG Project` with `git subtree split`, preserving all 3
commits that touched the project. One extra commit carried over the original spec,
`RAG_SERVICE_INSTRUCTIONS.md` (since removed from tracking; the owner keeps it locally),
and the `.env` / `.venv` ignore rules (they lived in the parent `rag/.gitignore`).

### Phase 1 — self-contained, configurable, containerised (complete)

**`backend/app/config.py` fully rewritten.** This was blocking everything: the old file
ran `os.environ["GEMINI_API_KEY"]` at import, so after extraction all 20 offline tests
died with a `KeyError` before collection, and a container without a key would
crash-loop with an opaque traceback.

Now a `pydantic-settings` `Settings` model behind a cached `get_settings()`. Nothing is
read at import. `settings.require_gemini_api_key()` raises `ConfigError` at the point of
use with an actionable message. Every call site was updated — `config.FOO` constants no
longer exist.

Note `cors_allow_origins` uses `Annotated[list[str], NoDecode]`. Without `NoDecode`,
pydantic-settings tries to JSON-parse the env var before validators run, so a plain
comma-separated string fails. Remember this if you add another list-typed setting.

**`backend/pyproject.toml`** replaces `requirements.txt` and `pytest.ini` (both deleted).
Pins the 7 dependencies actually imported. The old shared requirements file carried 11
packages for four unrelated exercises; 8 were unused here. Dropping
`sentence-transformers` drops `torch`: **5.4 GB → 402 MB**.

`torch`/`transformers` are optional because `langchain_core/language_models/base.py`
wraps the import in `try/except ImportError`. Do not add anything that hard-requires
them.

**Ruff** configured (line-length 120, matching existing style) and all 20 first-run
findings fixed. The only substantive one: `zip()` in `retriever.py` now passes
`strict=True`, so a length mismatch from Chroma fails loudly instead of silently
truncating results.

**`/health` and `/ready` added.** `/health` is dependency-free liveness. `/ready` checks
config present, reference DB readable, both Chroma collections non-empty; returns 503
with a per-check breakdown. It makes no Gemini call, so the ALB can poll it for free.

**CORS** now comes from config and defaults to `[]`. `allow_origins=["*"]` is gone, as
are the blanket method/header wildcards.

**Containers.** `backend/Dockerfile` is multi-stage (deps → corpus → runtime). The
corpus is baked in at build time; the running container does no startup work. The
ingest needs the embeddings API, so the key is a **BuildKit secret** for that one `RUN`
— verified it appears **zero times** in `docker history`. Runtime is non-root (uid
10001), app code root-owned, only `/srv/data` writable.

`frontend/Dockerfile` builds the Vite bundle and serves it from nginx with SPA fallback.
`VITE_API_BASE_URL` is inlined at **build** time, so it is a build arg, not a runtime
env var — this matters for the CloudFront deploy.

`docker compose up` works with only `GEMINI_API_KEY` set.

### Phase 1b — generation quality (complete)

**Root cause of the section-marker defect (§4.2) was the pattern corpus, not just the
prompt.** `sp_templates/rollback_auto.sql` was itself a complete
`CREATE OR REPLACE PROCEDURE` wrapper, there was no `main_script` pattern at all, and
`SYSTEM_PROMPT` rule 2 tells the model to follow each section's pattern. The model was
doing what it was told.

- New `sp_templates/main_script.sql` holds the procedure wrapper. `rollback_auto.sql` is
  now only the `EXCEPTION WHEN OTHERS` handler. `PATTERN_CATEGORIES` now equals
  `SECTION_ORDER` (7 categories; was 6, missing `main_script`).
- `SYSTEM_PROMPT` gives every section an explicit content contract and a short worked
  example showing all seven markers.
- `validator.py` adds three checks: `MAIN_SCRIPT` must declare a procedure; no section
  other than `MAIN_SCRIPT` and `ROLLBACK_MANUAL` may declare one (`ROLLBACK_MANUAL` is a
  separate `rollback_*` procedure by design); `MAIN_SCRIPT` under 25% of the largest
  other section is flagged (observed failure was 2–6%).
- `tests/test_pattern_templates.py` pins the corpus to the same contract, so a template
  cannot reintroduce the defect.
- `evals/test_generation_shape_eval.py`: full live pipeline over the 4 golden prompts,
  run concurrently (~3 min instead of ~11).

**Re-baseline** (4 golden prompts, run concurrently): all `sql_valid: true` with no
issues; procedure in `MAIN_SCRIPT` every time; `ROLLBACK_AUTO` 126–157 chars of handler
(was 4.3–5.1 KB of procedure). Section sizes in chars:

| Prompt | main_script | largest other | rollback_auto | latency |
|---|---|---|---|---|
| process_payroll | 940 | 1330 (temp_tables) | 136 | 61s |
| onboard_employee | 3960 | 669 (rollback_manual) | 126 | 119s |
| approve_timesheet | 5178 | 1856 (temp_tables) | 138 | 144s |
| terminate_employee | 3016 | 1569 (backup) | 157 | 96s |

`process_payroll` reproduces the `main_script.sql` pattern nearly verbatim — every
pattern is written around that same payroll example. Not a defect, but worth knowing.

Verified: 32/32 offline tests, ruff clean, 23/23 live evals (3m42s). **Superseded by
Phase 1c**, which replaced this section layout.

### Phase 1c — one executable procedure (complete)

Phase 1b left two problems. `full_script` could not run top to bottom: the
`PRE_VALIDATION` / `POST_VALIDATION` patterns were standalone `DO $$` blocks referencing
procedure parameters out of scope, and `ROLLBACK_AUTO` was a duplicated handler fragment.
And the pattern example *was* golden prompt #1, so that eval case measured copying.

**New section contract: one procedure spans the sections.** The `-- SECTION:` markers are
SQL comments, so they sit inside the dollar-quoted body:

- `PRE_VALIDATION` opens the procedure (header, `DECLARE`, `BEGIN`, guards).
- `TEMP_TABLES`, `BACKUP`, `MAIN_SCRIPT`, `POST_VALIDATION` are statements inside the body.
- `ROLLBACK_AUTO` is the real `EXCEPTION ... END; $$;` that closes it — no duplication.
- `ROLLBACK_MANUAL` is a separate `rollback_*` procedure.

Changes:

- All 7 templates rewritten as one worked example, `reassign_department_employees`, which
  is none of the golden prompts. A test pins that.
- `SYSTEM_PROMPT` rule 2 says the patterns are a *different* operation to learn idioms
  from, not copy. Contracts and a placeholder skeleton describe the new layout, and it
  forbids `$$` inside the body (even in comments: a `$$` anywhere ends the quote).
- `validator.py` checks are now structural, replacing 1b's size-ratio heuristic, which
  would false-positive on heavy staging: procedure declared only in `PRE_VALIDATION` /
  `ROLLBACK_MANUAL`; `PRE_VALIDATION` opens one; `ROLLBACK_AUTO` closes with `EXCEPTION`
  and `$$`; no `$$` in the body sections.
- **Fixed a real concurrency bug** in `retriever._get_client()`: it built a new Chroma
  `PersistentClient` per call, and concurrent construction races inside chromadb
  ("Could not connect to tenant default_tenant"). Reproduced with four parallel
  generations. Now one lock-guarded client per process. FastAPI runs sync handlers on a
  threadpool, so concurrent `/generate` requests were exposed to this too.

**Verified against real PostgreSQL 16** (throwaway `postgres:16-alpine` container,
reference schema ported from SQLite; not part of the repo or CI yet):

- The assembled templates compile; `CALL reassign_department_employees(1, 2)` moved 4
  employees; the manual rollback restored all 4; a bad target department raised and
  left no partial writes.
- All 4 golden generations compile. Called against seed data: `process_payroll(2)`
  created 10 entries; `terminate_employee_and_reverse_payroll` reversed 1 entry;
  `onboard_employee` inserted the employee and the hire salary row; `approve_timesheet`
  raised its own guard because the run it targeted was already finalized — correct
  behaviour. `onboard_employee` first failed on a non-null `employee_id`: an artefact of
  the port (SQLite `INTEGER PRIMARY KEY` auto-assigns, plain Postgres does not), fixed
  in the harness with identity columns.

**Re-baseline** (4 golden prompts, concurrent): all `sql_valid: true`, no issues.

| Prompt | pre_validation | main_script | rollback_auto | latency |
|---|---|---|---|---|
| process_payroll | 1163 | 893 | 113 | **232s** |
| onboard_employee | 1829 | 847 | 114 | 87s |
| approve_timesheet | 3830 | 643 | 115 | 142s |
| terminate_employee | 1200 | 587 | 136 | 44s |

`MAIN_SCRIPT` is no longer the largest section — by design, it now holds only the
mutation. The local `data/vector_store/` has been re-ingested.

### Phase 2 — microservice hardening (complete)

Measured before building, because the libraries did not behave as assumed:

- `ChatGoogleGenerativeAI` defaults to **`max_retries=6` and no timeout**. A hung
  generation could hang forever. Its `max_retries` is an attempt count, and its retry
  fires on timeouts too: `timeout=2, max_retries=3` against a never-answering server took
  10.2s over 3 connections.
- `GoogleGenerativeAIEmbeddings` had **no working timeout at all**: `request_options` is
  declared but never read, and a `client_args` timeout is overridden per request. It hung
  indefinitely. Fixed by swapping in a google-genai `Client` with `HttpOptions(timeout,
  retry_options)`; verified it times out and retries as set.
- langchain-google-genai re-raises Gemini 4xx (429 included) as
  `ChatGoogleGenerativeAIError` with the original as `__cause__`, but lets 5xx through
  raw. Error mapping walks the cause chain.

What was built:

- **`app/rag/llm.py`** builds every Gemini client (classifier, generation, embeddings
  for both retrieval and ingest). Classifier/embeddings: 20s timeout, library retry, 3
  attempts. Generation: 200s timeout (300s until the thinking-level change), library retry off, own retry — once, only on
  429/500/502/503, only if the attempt failed within 30s; streams only before any text
  was sent; plus an overall stream deadline. **Deviation from the plan:** langchain's
  `.with_retry()` filters by exception *type* only, so it cannot tell a 429 from a 400
  (same wrapper class) or a fast failure from a slow one. A 20-line loop does instead.
  No new dependency.
- **`app/errors.py`**: `ServiceError(status, code, message)` and one
  `to_service_error()` used by both the HTTP handlers and the SSE `error` event. Body
  `{"error": {code, message, request_id, details?}}`. Codes are listed in the module and
  the backend README. The 422 handler no longer echoes the input back.
- **Validation**: non-blank prompt ≤ `MAX_PROMPT_CHARS` (8000), `extra="forbid"`,
  bounded `conversation_id`, body ≤ `MAX_REQUEST_BYTES` (64 KB) → 413 before parsing.
  `StreamRequest` allows an empty prompt only with `is_regenerate` (the UI sends `""`).
- **`app/ratelimit.py`**: token bucket per client IP on the two generation endpoints,
  6/min burst 3, `Retry-After` on 429. `TRUST_FORWARDED_FOR` takes the *last*
  `X-Forwarded-For` entry (the one the ALB appends).
- **`app/middleware.py`** (pure ASGI): request ID (caller's `X-Request-ID` if sane, else
  generated; echoed back), body cap, one access line per request (healthy probes at
  DEBUG), last-resort JSON 500.
- **`app/observability.py`**: JSON logs with `request_id` via contextvar; prompts logged
  as length + 12-char hash only; the classifier's `reason` kept only for rejections (it
  paraphrases the request). One **EMF** line per generation: namespace `SPGenerator`,
  dimensions `Endpoint` x `Outcome`.
- **SSE heartbeat**: the stream runs on a daemon worker thread and sends `: keepalive`
  every 15s while the model thinks, and stops early on client disconnect. **This removes
  the streaming path's dependence on the ALB idle timeout.**
- **`/ingest` removed from HTTP.** `_sync_collection` upserts in place: repeated ingests
  stay at 2 segment dirs (`chroma.sqlite3` still grows ~0.25 MB per run from Chroma's
  internal log — minor).
- **Shutdown**: `--timeout-graceful-shutdown ${GRACEFUL_SHUTDOWN_S:-110}`,
  `--no-access-log`, compose `stop_grace_period: 2m`.
- Frontend `api.js` reads `error.message` from the new body shape.

Verified:

- 23/23 live evals (4m56s) through the new client factories. 69/69 offline tests (34 new across `test_api_hardening.py`, `test_observability.py`),
  ruff clean. Mutation-checked: allowing retries on 400/504, removing the heartbeat, and
  retrying after text was sent each turn a test red.
- Live, locally: a real stream sent 4 heartbeats and 65 chunks, returned
  `sql_valid: true`; `/health` answered in ~1ms three times during the generation; every
  log line JSON with the request ID; prompt text found 0 times in the log.
- Container: rebuilt; SIGTERM sent 10s into a live generation — uvicorn waited ~20s for
  it, the client got `done`, then the app shut down. CORS and `x-request-id` headers on
  the stream response.

### Verified end to end (Phase 1)

- 20/20 offline tests pass with `GEMINI_API_KEY` unset.
- 15/15 live evals pass (~95s).
- `ruff check` clean.
- Image builds; `/health`, `/ready`, `/schema`, `/api/docs` all 200 in the container.
- CORS allows the configured origin, refuses others.
- A real generation through the container returned a valid 7-section script in **159s**,
  retrieving `payroll_runs` and `payroll_entries` as the top two tables.

---

## 4. Measurements that constrain the remaining work

### 4.1 Generation took 44–232 seconds (now ~15s at thinking level `low`)

Measured repeatedly. Telemetry from live runs: `avg_generation_latency_ms = 171,722`;
classifier ~4.7s, retrieval ~0.6s, generation ~172s.

Phase 1b/1c re-baselines (4 golden prompts each, run concurrently) measured **44–232s**
end to end. The 232s run is a new worst case. Plan for ~240s: the generation timeout must
exceed it, and the ALB's `idle_timeout = 300` leaves only modest headroom.

**Update (thinking level).** Profiling showed local work (retrieval, prompt
assembly, validation) is ~0.13s of a ~255s request; the rest is Gemini, almost
all of it silent reasoning before the first token. `GENERATION_THINKING_LEVEL`
now defaults to `low`: 11–16s per generation on the golden prompts instead of
39–162s, with every script still compiling and running in Postgres (4 prompts,
one run — see README "Thinking level"). The constraints below still hold for
`auto` and for the fallback when a model rejects the level. The generation
timeout was then lowered from 300s to 200s (owner's call): enough for `auto` on
the golden prompts (39–162s), though the one 232s outlier seen earlier would
now time out.
`CLASSIFIER_THINKING_LEVEL` (also `low`) takes the classifier from ~3.9s to
~2.4s median with identical answers over 23 scenarios × 3 runs.

**This is the dominant architectural constraint.**

- ALB's default idle timeout is 60s and would kill every request. **Set
  `idle_timeout = 300`.**
- API Gateway cannot be used at all — hard 29s cap, not configurable.
- Health-check and deregistration-delay settings must not inherit request-length
  assumptions, or deploys will cut off live generations.
- Any retry wrapper must not blindly retry a 172s call.

Decision taken: **keep SSE and tune timeouts.** No async job API.

### 4.2 The section-marker defect (fixed in Phase 1b — see §3)

Reproduced on two different prompts. The model emits the full
`CREATE OR REPLACE PROCEDURE <main_operation>` under `-- SECTION: ROLLBACK_AUTO`
(4.3 KB and 5.1 KB respectively) while `-- SECTION: MAIN_SCRIPT` gets a 98–240 char
fragment. `rollback_auto` contained no `ROLLBACK` at all.

Both outputs reported `sql_valid: true`, because `validator.py` only checks marker
presence, non-emptiness and balanced parens/`$$`.

The UI never renders `sections` (it shows `full_script` via `CodeBlock`), so this is
invisible in the demo — but it is wrong in the API contract, and the seven-section
structure is the project's central claim.

### 4.3 There are TWO LLM construction sites (consolidated in Phase 2 — `app/rag/llm.py`)

- `generator.py` `_build_llm()` — used by generation and streaming.
- `generator.py` `classify_request()` — **inlines its own `ChatGoogleGenerativeAI`** at
  `temperature=0`.

Timeouts, retries and test mocks must cover **both**. Patching `_build_llm` alone still
lets the classifier make a live call.

### 4.4 Ingest leaves orphaned Chroma segments (fixed in Phase 2)

`_replace_collection()` hard-deletes and recreates both collections every run, minting
new segment UUIDs. Chroma does not reclaim the old directories. The old local store had
**10 segment dirs / 13 MB for 15 embeddings; only 2 were live**. A clean ingest gives
3.0 MB / 2 dirs.

`.dockerignore` keeps the local store out of the image, so the image is unaffected. But
`_replace_collection` should still clean up after itself.

### 4.5 Image is 865 MB

Higher than the ~500 MB originally estimated. 151 MB of that is `kubernetes` (83 MB) and
`onnxruntime` (68 MB), hard dependencies of chromadb for distributed mode and its default
embedding function. **Verified neither is ever imported at runtime.**

Pruning them from `site-packages` was considered and **rejected**: it risks an
`ImportError` on an untested chromadb path, for a saving worth pennies of ECR storage.
Do not prune without a strong reason.

### 4.6 The RAG layer is already stateless

`generate_stored_procedure`, `stream_stored_procedure` and `classify_request` all already
take `prior_messages` as plain dicts. **All store coupling lives in `main.py` alone.**
This makes Phase 3 much smaller than it looks.

---

## 5. Decisions taken with the owner — do not re-litigate

| Decision | Choice |
|---|---|
| Repo | Standalone public GitHub repo (done) |
| Latency | Keep SSE, tune timeouts (ALB idle 300s). No async job API |
| Section bug | Fix early, before hardening. Re-baseline evals |
| AWS region | `ap-south-1` (Mumbai) |
| AWS apply | Build and validate freely. **`terraform apply` only on the owner's explicit in-session go-ahead**, then destroy |
| UI history | Keep the sidebar, backed by localStorage |
| Metrics | CloudWatch EMF via logs (not a `/metrics` endpoint) |

Budget is small — roughly ₹1,000–2,000. Bring the stack up for demos and destroy it
afterwards. Never leave anything running that costs money without saying so.

---

## 6. What to do next

### Phase 3 — stateless and pluggable

**Step 1 done — stateless backend.** Both stores, `/conversations*`, `/metrics` and the
`isolated_app_db` fixture are gone; `DATA_DIR`/`APP_DB_PATH` settings removed. The API is
`/v1/generate`, `/v1/generate/stream`, `/v1/schema` (probes stay unversioned). Body:
`messages: [{role, content?, kind?, full_script?}]`, last one the user's; validated per role,
unknown fields refused, so the old `{prompt, conversation_id}` shape gets a 422. The SSE
`start` event is gone (it only carried `conversation_id`); `rejected` and `done` no longer
carry ids. `MAX_REQUEST_BYTES` raised 64 KB → 256 KB because the caller now resends every
earlier script (~8 KB each): about 25 turns. API tests ported to `/v1` (42 in
`test_api_hardening.py`).

**Step 2 done — frontend on `/v1`, history in localStorage.** `src/conversationStore.js`
keeps conversations under `sp-generator.conversations.v1`, every storage access guarded
(theme's too), oldest conversations dropped if the quota is hit; `toApiMessages` reduces
saved messages to the request shape. A reply is saved only once `done`/`rejected`, so a
failed one is never sent back as history. Regenerate resends without the last answer and
keeps the old one until the new one lands. `RetrievedContextPanel` no longer dereferences
`chunk.metadata` blindly. `sections` is not kept client-side (the markers are in the
script); the API still returns it. Verified: oxlint clean, Vite build, a Node check of the
store (quota trimming, broken storage, malformed data, request shape), and the UI's request
flow against the rebuilt containers. **Not clicked through in a browser.** Conversations
from the old server-side `app.db` are not migrated.

**Step 3 done — adapters.** `app/adapters/`: `base.py` (Protocols `LLMProvider`,
`VectorStore`, `SchemaSource` + `RetrievedChunk`/`Table`/`Column`/`ForeignKey`), `gemini.py`
(was `app/rag/llm.py`; now also owns the thinking-level fallback, inside `complete`/`stream`),
`chroma.py` (client singleton + in-place sync, moved from retriever/ingest), `sqlite_schema.py`
(the one schema reader; replaces `app/db/reference_schema.py` and ingest's own copy; opens the DB
read-only). Selected by `LLM_PROVIDER` / `VECTOR_STORE` / `SCHEMA_SOURCE`, each a one-value
Literal. The retry policy moved to `app/rag/retry.py` (provider-independent). Verified that the
schema and pattern chunk texts and the `/v1/schema` output are byte-identical to before, so
embeddings and the API did not change. Tests now fake `app.adapters.llm_provider`.

**Step 4 done — contract and architecture note.** Request/response models live in
`app/api_models.py`; routes declare them, so the spec has response schemas, typed error
responses (the service's `ErrorResponse`, replacing FastAPI's default 422 schema) and an SSE
description with an example. `docs/openapi.json` is generated by `python -m app.openapi_export`.
`tests/test_contract.py` (Phase 4's contract test) checks both directions: generated spec ==
checked-in file, and real responses validate against the checked-in spec with exactly its
fields; the spec's two request examples are posted and accepted. `jsonschema` is now a pinned
dev dependency (it was only present transitively, via chromadb). `docs/ARCHITECTURE.md`
covers the gateway, where auth sits, caller-owned state, swapping adapters, with a context
diagram and a request-flow sequence diagram. **The Mermaid diagrams were not rendered** (the
mermaid-cli image pull was not permitted here); check them in GitHub's or VS Code's preview.
`planning/00-overview.md`'s `app.db: history` diagram is annotated as superseded. Test fakes
shared by the API and contract tests are in `tests/fakes.py`.

**Phase 3 is complete.**

Original plan for this phase:

- **Delete `app/db/conversation_store.py` and `app/db/telemetry_store.py`.** Remove
  `GET /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}` and
  `GET /metrics`. `_prior_messages_for` goes. `get_last_user_message` is already dead
  code (nothing imports it).
- **`/v1/generate` and `/v1/generate/stream`** accept `messages: list[Message]` in the
  body. Responses drop `conversation_id` and `message_id`. **`is_regenerate`
  disappears** — the client just resends without the last assistant turn.
  `evals/test_classifier_eval.py`'s `PAYROLL_PRIOR_MESSAGES` is already in the right
  shape to model the request body on.
- **Three adapters**, selected by configuration, no speculative extras: `LLMProvider`
  (Gemini), `VectorStore` (Chroma), `SchemaSource` (SQLite). Note that
  `db/reference_schema.py:list_tables()` and `rag/ingest.py:_build_schema_chunks()`
  duplicate the metadata-join with slightly different output shapes — that duplication
  is the seam to consolidate behind `SchemaSource`.
- **Checked-in OpenAPI spec** with request/response examples.
- **`docs/ARCHITECTURE.md`**: gateway in front, where auth would sit, caller-owned
  state, adapter swapping, Mermaid request flow. `planning/00-overview.md`'s diagram
  ends in an `app.db: history` node that this phase invalidates — update or annotate it.

**Frontend work** (all of `frontend/src/`):

- Add `conversationStore.js` keeping the current server-side shape in localStorage.
  Reuse the lazy-initializer + effect pattern already used for theme in `App.jsx`, but
  wrap storage access in `try/catch` — the theme code does not, and private-mode or
  blocked site data will throw.
- `api.js` loses `listConversations`, `getConversation`, `deleteConversation`, and
  `streamGenerate` sends `messages` instead of `conversation_id`/`is_regenerate`. All
  URLs gain the `/v1` prefix.
- `App.jsx` is the bulk of it: `activeConversationId` currently arrives from the `start`
  SSE event, and `handleRegenerate` is guarded by `if (!activeConversationId) return;`
  and sends `prompt: ""`. Both go.
- `Sidebar.jsx` is purely presentational and needs no change beyond its data source.
- Fix `RetrievedContextPanel.jsx`: it dereferences `chunk.metadata` without optional
  chaining and will throw on a malformed chunk.
- `sections` is carried in App state but rendered nowhere. Either surface it or drop it
  — do not leave it as dead weight while claiming the seven-section structure is the
  product.

### Phase 4 — tests

- The 25 pure tests in `test_section_parser.py`, `test_validator.py` and `test_pattern_templates.py`
  survive untouched.
**Status: done except the evals CI job (Phase 6).** 99 offline tests.

- The 10 tests in `test_conversation_store.py` and `test_telemetry_store.py` are deleted
  with the stores, and the `isolated_app_db` fixture with them. **Done (Phase 3 step 1).**
- **API tests** with `TestClient` and **both** LLM sites mocked: happy path, classifier
  rejection, validation errors, upstream 503, SSE event shape. **Done**: written in Phase 2,
  ported to `/v1` in Phase 3 (`tests/test_api_hardening.py`, 42 tests, fakes in
  `tests/fakes.py`). The mocking pattern that
  works is patching the adapter factories (`app.adapters.llm_provider`, and
  `app.rag.generator.retrieve_context` for retrieval) -- since Phase 3 step 3; before that it
  was `app.rag.generator._build_llm` and `app.rag.retriever._get_embeddings` -- because
  everything resolves `config.`-style settings at call time, not import time.
- **One contract test** pinning the `/v1` response shape to the checked-in OpenAPI spec.
  **Done in Phase 3 step 4** (`tests/test_contract.py`).
- Evals stay live-API and out of the default run. **`test_retrieval_eval.py` needs a
  populated vector store**, so its CI job must build the index before running.

### Phase 5 — Terraform (`infra/`, ap-south-1)

**Redesigned (2026-11-15): HTTPS on the owner's domain, no CloudFront.** The first `make up`
failed at CloudFront (*"Your account must be verified before you can add new CloudFront
resources"* — a new-account restriction); the partial stack was destroyed. The owner owns
`abhishek.gen.in` (DNS at GoDaddy, `ns03`/`ns04.domaincontrol.com`) and chose
**`sqlgen.abhishek.gen.in`**, delegated to Route 53 ($0.50/month, agreed). Now:

- `account/` adds `modules/domain`: the Route 53 zone and a DNS-validated ACM certificate,
  both long-lived (stable name servers; the certificate renews itself).
- `demo/`: the ALB serves HTTPS (TLS 1.2/1.3 policy) with that certificate, 80 → 443
  redirect; one Fargate task runs `api` and `ui` (nginx) containers; `/v1/*` and `/api/*`
  → API, the rest → UI. A Route 53 alias points the domain at each new ALB.
  `TRUSTED_PROXY_HOPS=1`. Two ECR repositories. `modules/frontend` (S3 + CloudFront),
  the origin-verify header and the CloudFront prefix list are gone.
- **Spec deviation:** the original spec asks for "S3 plus CloudFront for the demo
  UI" and a "CloudFront URL" output. Replaced by the UI container behind the ALB because
  CloudFront is blocked on this account; the owner chose the domain route. CloudFront can
  return later as a CDN in front of the ALB once the account is verified.
- First-time order: `make dns-zone` → 4 NS records named `sqlgen` at GoDaddy →
  `make dns-check` → `make account-apply` (waits for the certificate) → `make up`.
- Verified offline: both stacks validate; `account` plans 4 adds (zone, cert, validation
  record, validation); the UI image built with an empty API base serves `/` with 200 and
  compiles the base to `""`. The demo stack cannot be planned until the zone and
  certificate exist (it looks them up).

**Earlier status (2026-11-14, CloudFront design, superseded above):** written, validated
and planned. Decision then: **no domain → CloudFront in front of both UI and API**. Two root stacks: `infra/demo` (34 resources, `make
up`/`make down`) and `infra/account` (budget adopted via `import` + kill switch, 10 resources;
plan: 1 import, 10 add, 1 in-place change adding the SNS topic to the 95% notification).
Departures from the original plan below, each for a reason:
- Task size 0.25 vCPU / 512 MB, not 0.5 / 1 GB: measured peak 143 MiB and ~27% of a core
  during two concurrent generations. A variable.
- `TRUST_FORWARDED_FOR` (bool, last XFF entry) replaced by `TRUSTED_PROXY_HOPS` (int): behind
  CloudFront + ALB the client is the second-to-last entry, so the old setting would have
  rate-limited per CloudFront edge rather than per client. Set to 2 in the task.
- The UI is built with `VITE_API_BASE_URL=""` (same origin); `api.js` now uses `??` so an
  empty value means same-origin instead of falling back to localhost. Verified in the bundle.
- ALB idle timeout 300s kept; CloudFront origin read timeout 60s (max without a quota
  increase) now bounds non-streaming `/v1/generate`. The stream heartbeats every 15s.
- Costs from the Price List API (not memory): ≈ $0.052/h ≈ $1.25/day while up.
Verified offline: `terraform validate` both stacks, `terraform plan` both stacks against the
account, the kill-switch Lambda's logic (3 tests with a fake boto3), the scripts parse. **Not
yet verified: anything that needs `apply`** — the stack coming up, the image running on
Fargate, SSE through CloudFront, the kill switch firing.

Original plan:

Modules: `network`, `ecr`, `service`, `frontend`.

ECS Fargate (0.5 vCPU / 1 GB), ECR, ALB with health check on `/ready`, Secrets Manager
for `GEMINI_API_KEY`, CloudWatch log group with short retention, least-privilege IAM,
S3 + CloudFront for the UI. Public subnets plus security groups, **no NAT gateway**
(cost). Local state, stated as such in the README.

**`idle_timeout = 300` on the ALB is the single most important setting here.** See 4.1.
Since Phase 2 the streaming path sends a heartbeat every 15s, so it no longer depends on
the idle timeout. Non-streaming `/generate` still does. With the generation timeout at
200s, a typical request is ~15s and even an `auto`-level one fits inside 300s; only the
pathological case (classifier and embeddings both exhausting their retries, ~65s each,
and then a slow generation) could pass it. **`idle_timeout = 300` is therefore enough;
the earlier ~420s suggestion is withdrawn.** Also set the ECS task `stopTimeout = 120` (above
`GRACEFUL_SHUTDOWN_S = 110`) and ALB `deregistration_delay` to ~120s, or deploys will cut
in-flight generations sooner than they need to.

Variables: region, image tag, desired count. Outputs: ALB URL, CloudFront URL.
Cost guard: `make up` / `make down`.

### Phase 6 — CI/CD

**Status (2026-11-15): done and verified end to end on GitHub. Phase 6 is complete.**
Evals workflow run by hand on `cbf1860`: green in 61s (vector store built in CI in 5s, evals
48s, `eval-results` JUnit artifact uploaded; its contents were not read here — that needs
an authenticated download). Deploy run 4 (`cbf1860`): CI green (4 jobs, ~20s each); deploy 292s — images built
and pushed in 87s, the live service rolled from task definition revision 2 to 3 (both
containers on `cbf1860c082b`) in 199s with the site up throughout; afterwards `/` 200 and a
real `/v1/generate` returned valid SQL in 22s. Run 3 had failed on a missing
`ecr:DescribeRepositories` grant (CloudTrail), now added. Originally: written, checked
locally. `ci.yml`
(PRs/branches, and called by deploy), `deploy.yml` (push to main: CI → build and push
images → roll the service if the demo is up), `evals.yml` (manual; nightly if
`NIGHTLY_EVALS=true`). Decisions: ECR moved to the account stack so pushes work while the
demo is down; deploy is via a GitHub OIDC role (`modules/github_deploy`) scoped to this
repo's main branch, ECR push for two repos, and rolling one service — no Terraform in CI
(local state); `scripts/push-images.sh` is shared with `make up`; `roll-service.sh`
registers a copy of the live task definition with new images. No `environment:` on the
deploy job (it would change the OIDC subject). Dependency audit findings handled:
pydantic-settings 2.13.1 → 2.16.3 (CVE-2026-58203); four chromadb advisories ignored with a
documented reason (they concern Chroma's HTTP server, which is never run; no fix exists);
three high npm advisories in dev tooling fixed by `npm audit fix` (lockfile only). Checked
locally: lint, 104 tests, Terraform fmt/validate without a backend, the API deps-stage
build, the task-definition jq transform, YAML structure. **Not checked: actionlint** (its
download was blocked here), and nothing has run on GitHub yet. Needs: `make account-apply`
(adds ECR + OIDC role), the `GEMINI_API_KEY` secret and `AWS_DEPLOY_ROLE_ARN` variable.

First runs on GitHub (2026-11-15): run 1 failed at "Set up job" — `astral-sh/setup-uv@v10`
does not exist (no major tags); pinned to the v10.3.3 commit. Run 2: **all four CI jobs
green**; deploy failed to assume the role. CloudTrail showed GitHub's OIDC subject is now
`repo:abhishekar10@66424913/store-procedure-generator-service@1456351902:ref:refs/heads/main`
(owner and repo IDs included), not the documented `repo:owner/repo:ref:...`; the trust
policy now matches the ID-based subject exactly.

Original plan:

- `ci.yml`: ruff, pytest, `terraform fmt`/`validate`, Docker build, `pip-audit`.
- `deploy.yml`: build and push to ECR tagged with the commit SHA, update ECS, wait for
  stability. **GitHub OIDC**, no long-lived access keys.
- `evals.yml`: manual plus optional nightly, uploads results as an artefact.
- Status badge and a "how it deploys" section in the README.

**CI needs `GEMINI_API_KEY` as a repository secret** — the Docker build runs the ingest.
Pass it with `--secret id=gemini_key,env=GEMINI_API_KEY`, never as a build ARG.

---

## 7. Known issues not yet addressed

- `planning/*.md` are historical design notes, partly superseded. `00-overview.md` and
  `01-frontend.md` carry "superseded" banners. Do not treat them as current spec.
- Execution against real Postgres is verified by hand (Phase 1c, the thinking-level
  benchmark, `docs/sample-output/`), not automated. A CI job that loads the schema into
  a Postgres service container and compiles and calls each eval generation would make it
  a regression check (planned, not built). The scratch harness that did it by hand
  picked arguments heuristically; a CI version needs per-prompt call fixtures.
- Retrieval precision is loose on some prompts — all 9 tables live in one domain, so
  embeddings cluster tightly. The eval thresholds (recall ≥ 0.5 per case, ≥ 0.6 mean)
  are regression canaries, **not** a claim that retrieval is good.
- The classifier accepts requests for other databases: a live "Language: mysql" request
  on the demo got PostgreSQL with no warning. **Fix planned in `docs/ROADMAP.md` v1.0.x**
  (reject with "MySQL support is planned"); MySQL itself is v1.1.
- Generated safety checks can miss a wrong request: the same live test asked for a
  backwards formula, and the script set all ten seed payroll entries' deductions negative
  (rollback restored them). Post-validation checked only its own staging table, not the
  invariant. **Fix planned in `docs/ROADMAP.md` v1.0.x.**
- **Roadmap (owner, 2026-11-15):** MySQL support (v1.1) and own-schema generation with a
  visual schema editor (v1.2) — see `docs/ROADMAP.md`, including the design questions each
  raises (MySQL's handler position vs the section order; where an edited schema lives in
  a stateless service).
- The demo UI has been verified by build, lint, a Node check of its storage module and
  its request flow against the containers, but **not clicked through in a browser**.
- The Mermaid diagrams in `docs/ARCHITECTURE.md` have not been rendered by a tool.

Resolved in the documentation pass (2026-11-14): stale README setup sections (all three
READMEs now describe this repo; there is a root README), the citations of
`06 - Interview Questions/...`, the LAN IP in `frontend/.env.local` (the file no longer
exists), and the saved generation in `stored-procs/` — that one predated the Phase 1b
fix (it used `DO $$` blocks), so it was replaced by a fresh, Postgres-checked sample in
`docs/sample-output/` rather than moved.

---

## 8. State right now

- Everything through Phase 4 (except the evals CI job) is pushed to `origin/main`.
  There are no git credentials configured on this machine — no helper, no
  `~/.git-credentials`, no SSH keys — so the owner pushes from VS Code's source control
  panel, not the CLI.
- **Demo stack: down** (destroyed after the CloudFront failure; redesigned for
  `sqlgen.abhishek.gen.in` — see Phase 5).
- **Account stack: up** — budget `monthly-1000-inr` with the kill switch on 95%; the
  kill switch was triggered by hand with nothing deployed and reported "already down" for
  both targets; the report email subscription is confirmed.
- **AWS is set up for the CLI (2026-11-14).** Profile `sp-generator`: IAM Identity
  Center (SSO session `personal`), role `AdministratorAccess`, region `ap-south-1`,
  verified with `aws sts get-caller-identity`. When the session expires:
  `aws sso login --profile sp-generator`. Root has MFA and no access keys.
- **Budgets** (the only AWS resources created so far, both free): the console's "My
  Zero-Spend Budget" ($0.01), and `monthly-1000-inr` — $11.50/month (≈ ₹1,000; the
  Budgets API accepts only USD for this account), email alerts on *actual* spend at 50%,
  75% and 95%, to the owner's address. Created by Claude at the owner's request.
  Cost data lags by hours, so alerts are a backstop, not a real-time cutoff.
- The compose stack may still be running (`docker compose ps`). `docker compose down`
  to stop it. A scratch Postgres container `sp-pg` may also be running
  (`docker rm -f sp-pg`).
- `backend/db/reference.db` and `backend/data/vector_store/` exist locally and are
  gitignored. Rebuild with `db/build_db.py` and `python -m app.rag.ingest`.

### Before Phase 5

Done: CLI credentials (SSO, not root), root MFA, the budget. Still open: **the HTTPS
decision** (below). `terraform init/validate/plan` run freely; `terraform apply` waits
for the owner's go-ahead in that session.

**Kill switch — requested by the owner, to build in Phase 5.** AWS Budgets' own actions
can only attach a deny policy or stop EC2/RDS instances; they cannot stop Fargate, an
ALB or CloudFront. So: the budget's 95% alert also notifies an SNS topic → a small
Lambda that sets the ECS service's desired count to 0 and deletes the ALB (the hourly
costs), and emails that it fired. `make up` recreates both (Terraform sees them missing).
Same lag caveat as the alerts. Rough running cost of the demo stack, from memory and
**not checked against current ap-south-1 prices**: ~₹100–150/day (Fargate 0.5 vCPU /
1 GB, ALB hours, public IPv4 addresses) — so ₹1,000 is about a week left running.
Gemini usage is billed by Google, outside this budget.

**HTTPS / mixed content — needs a decision.** The UI on CloudFront is HTTPS. An ALB
without a domain serves only HTTP (an ACM certificate needs a domain), and browsers
block an HTTPS page calling an HTTP API. Options:

- **(a) CloudFront in front of the ALB too** — one CloudFront distribution, `/` → S3,
  `/v1/*` → ALB. HTTPS everywhere on the `*.cloudfront.net` name, no domain needed, and
  no CORS (same origin). Caveat: CloudFront's origin read timeout is 30s by default (60s
  max without a quota request). The stream is fine — it sends status events at once and
  a heartbeat every 15s — but non-streaming `/v1/generate` must answer within it
  (typically ~15s; `auto` thinking or slow upstreams would exceed it).
- **(b) A domain the owner controls** (Route 53 or external DNS) + ACM certificate on
  the ALB, and the UI calls `https://api.<domain>`.

Recommendation: (a) — no domain cost, simplest, and the UI uses the stream anyway.

---

## 9. Working rules

- Small, reviewable commits with clear messages. Do not rewrite history.
- Run tests and linters after each phase. **Report honestly what passed and what did
  not.**
- Do not add dependencies or abstractions without saying why.
- **Do not run `terraform apply` or create AWS resources without the owner's explicit
  go-ahead in that session.** Plan and validate freely.
- Do not commit secrets. `.env` is gitignored; `.env.example` is the template.
- If something in the spec conflicts with what the code needs, stop and ask rather than
  guessing.
