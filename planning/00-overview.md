# RAG Stored Procedure Generator — Overview

This is a planning note, not a spec. Treat everything here as a suggested starting
point you can push back on as you build — nothing is locked in except the three
decisions marked "Decided" below.

## What this project demonstrates

The point of this build is to show RAG judgment, not just "call an LLM." The
interesting part is that there are **two distinct retrievable corpora** feeding
one generation:

1. A **schema corpus** — grounds the LLM in what tables/columns actually exist,
   so it doesn't hallucinate column names.
2. A **pattern corpus** — a small, self-authored library of best-practice SQL
   snippets (pre-validation, temp tables, backup, post-validation, rollback)
   that ground the LLM in *how* a well-written stored procedure is structured.

Retrieving from both and merging them into one prompt is the crux of the demo.
Worth designing the retrieval/merge step carefully — it's the part most likely
to get evaluated closely.

## Decided so far

- Reference schema DB is **metadata-only** (SQLite) — nothing gets executed
  against it. SQL dialect is a per-request parameter, not tied to a real engine.
- Generation output is a **single script with comment-marker sections**
  (`-- SECTION: PRE_VALIDATION`, etc.), parsed server-side into a dict for the
  frontend to render as tabs.
- Orchestration uses **LangChain + langchain-google-genai**, consistent with
  `04 - Data Ingestion to Vector DB Pipeline`.

## Suggested high-level flow

```mermaid
flowchart LR
    U[User plain-English prompt] --> B[FastAPI /generate]
    B --> R1[Retrieve schema chunks<br/>Chroma: schema_chunks]
    B --> R2[Retrieve pattern chunks<br/>Chroma: pattern_templates]
    R1 --> M[Merge context + prompt + dialect]
    R2 --> M
    M --> G[Gemini generation]
    G --> P[Section parser<br/>splits on -- SECTION: markers]
    P --> H[(app.db: history)]
    P --> F[React UI: tabbed sections +<br/>retrieved-context panel]
```

## Suggested folder layout at the project root

```
05 - RAG Project/
  planning/     <- you are here
  frontend/     <- React + JSX
  backend/      <- FastAPI + RAG pipeline
  db/           <- schema seed scripts, sample data, migration notes
```

Read `01-frontend.md`, `02-backend.md`, `03-db-and-rag.md`, and
`04-genai-prompting.md` for the per-component notes. `05-open-questions.md`
tracks things still worth deciding before or during the build.
