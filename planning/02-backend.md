# Backend Notes (FastAPI)

Suggestions only. The route list and module names below are a starting
skeleton to react to, not a contract.

## Suggested routes

- `POST /generate` — the core pipeline: retrieve schema chunks, retrieve
  pattern chunks, merge into a prompt, call Gemini, parse sections, persist to
  history, return to the frontend.
- `GET /schema` — lists reference-DB tables/columns, for the frontend's schema
  explorer (if you build one) and useful for manual debugging regardless.
- `GET /history` / `GET /history/{id}` — past generations from `app.db`.
- `POST /ingest` — dev-time endpoint (or a standalone script instead of a
  route — worth weighing which is more convenient) that (re)embeds the schema
  corpus and pattern corpus into Chroma. Doesn't need to be user-facing.

## Suggested module layout

```
backend/
  app/
    main.py                 # FastAPI app + route wiring
    config.py                # env vars, paths, model names
    rag/
      retriever.py            # Chroma queries against both collections
      generator.py             # prompt assembly + Gemini call
      section_parser.py        # splits the -- SECTION: markers into a dict
      ingest.py                # embeds schema + pattern corpus
    db/
      reference_schema.py      # read-only queries against reference.db
      history_store.py         # read/write against app.db
    sp_templates/               # the authored pattern library (source docs)
```

Keeping `rag/` and `db/` as separate concerns inside the backend (rather than
one flat `services/` folder) will probably make it easier to reason about
what's "retrieval logic" vs "plain data access" as the pipeline grows.

## Things worth thinking through

- **Retrieval merge strategy** — how many chunks from each collection get
  pulled in (e.g., top-3 schema + top-2 pattern per category), and whether
  pattern retrieval should be per-category (always pull one pre-validation
  snippet + one rollback snippet, etc.) rather than a single similarity query
  across the whole pattern corpus. A per-category pull is more deterministic
  and probably a better fit here than pure similarity search, since you
  usually want *all* six pattern categories represented, not just the closest
  ones.
- **Prompt assembly** — where the merged context + user prompt + dialect
  instructions get combined. Isolating this in `generator.py` rather than
  inlining it in the route handler will make it much easier to iterate on
  prompt wording without touching route logic.
- **Section parser robustness** — the LLM might occasionally omit a section
  marker or drift on formatting. Worth deciding upfront how the parser should
  behave if a marker is missing (empty section vs. error vs. best-effort
  fallback) rather than discovering it live.
- **Error handling** — what happens on a Gemini API failure or empty
  retrieval (e.g., prompt doesn't match any known table)? Returning a graceful
  "no relevant tables found" is probably better than forcing a generation with
  no grounding.
