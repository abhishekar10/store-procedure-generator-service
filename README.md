# Stored Procedure Generator Service

A small service that turns a plain-English request -- "process payroll for a
given pay period" -- into one deployable PostgreSQL (PL/pgSQL) stored
procedure, grounded by retrieval over a reference HR/payroll schema and a
library of house-style SQL patterns.

It is built as a demonstration of an enterprise-style microservice rather
than an end-user product: a versioned contract, no state of its own,
swappable dependencies, typed errors, timeouts and retries, structured logs
and metrics, and a container image. A React UI is included as a demo client.

## What a generation looks like

Every response is one script with seven marked sections: pre-validation,
temp tables, backup, the main change, post-validation, an automatic rollback
(an exception handler), and a separate manual-rollback procedure. See
[`docs/sample-output/`](docs/sample-output/) for an unedited example and how
it was checked against Postgres.

## Run it

Needs Docker and a [Google AI Studio](https://aistudio.google.com/apikey) API key.

```bash
cp .env.example .env          # then set GEMINI_API_KEY in .env
docker compose up --build     # UI http://localhost:5173, API http://localhost:8000
```

The key is used twice: at build time, to embed the two corpora into the
image (passed as a BuildKit secret, so it is not left in any layer), and at
run time, for generation. API docs are at http://localhost:8000/api/docs.

```bash
curl -s localhost:8000/v1/generate -H 'content-type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Process payroll for a given pay period"}]}'
```

A generation takes about 15 seconds.

## Repository

| Path | What |
|---|---|
| [`backend/`](backend/README.md) | The FastAPI service: API, RAG pipeline, adapters, tests and evals |
| [`frontend/`](frontend/README.md) | The demo UI (React + Vite), served by nginx in compose |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it fits into a larger system: gateway and auth, caller-owned state, adapters, request flow |
| [`docs/openapi.json`](docs/openapi.json) | The `/v1` contract, checked in and pinned by a test |
| [`docs/sample-output/`](docs/sample-output/) | An example generation |
| [`MIGRATION_STATUS.md`](MIGRATION_STATUS.md) | Working log: what is done, measurements, decisions, what is next |
| `planning/` | Original design notes, partly superseded -- not the current spec |

## Design in brief

- **Stateless.** The caller sends the conversation so far with each request
  and keeps the results; the service stores nothing. The demo UI keeps its
  history in the browser.
- **Grounded.** Schema chunks are retrieved by similarity; one pattern per
  section is always included. A classifier call rejects requests that are
  not database operations before any retrieval or generation runs.
- **Swappable.** The model (Gemini), vector store (Chroma) and schema source
  (SQLite) sit behind three interfaces, selected by configuration.
- **No auth by design.** The service is meant to sit behind a gateway or
  load balancer that authenticates callers. Where that sits is described in
  the architecture note.

## Tests

```bash
cd backend
.venv/bin/python -m pytest -q          # 99 offline tests, ~2s, no API key
.venv/bin/python -m pytest evals/ -q   # 23 live-API evals, ~1 min, costs tokens
```

The offline suite covers the API end to end with the model faked, plus a
contract test pinning responses to `docs/openapi.json`. Setting up the
virtualenv is in [`backend/README.md`](backend/README.md).

## Status

Built: the service, its hardening, the stateless `/v1` API, the adapters,
the checked-in contract, the container images and local compose.

Planned, not built: Terraform for AWS (ECS Fargate behind an ALB, the UI on
S3 + CloudFront) and CI/CD on GitHub Actions. See
[`MIGRATION_STATUS.md`](MIGRATION_STATUS.md).
