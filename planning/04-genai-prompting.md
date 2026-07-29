# GenAI / Prompting Notes (Gemini)

Suggestions only.

## Models

- Generation: `gemini-flash-latest` (an alias that tracks Google's current
  recommended flash model rather than a pinned version) -- `gemini-2.0-flash`
  was tried first per the original plan but returned 404 as deprecated on
  this API key despite still appearing in `client.models.list()`, so the
  implementation uses the alias to avoid re-breaking on the next model
  deprecation.
- Embeddings: `gemini-embedding-001` (via `langchain-google-genai`) for both
  the schema corpus and the pattern corpus, so everything stays on one
  provider — consistent with what's already set up from project 04.
  (`text-embedding-004` isn't available on this API key/version — confirmed
  via `client.models.list()` — so the implementation uses `gemini-embedding-001`,
  the current stable embedding model.)

## Suggested prompt shape

Roughly: system framing ("you are generating a production-grade PL/pgSQL
stored procedure following these six sections...") + retrieved schema chunks
+ retrieved pattern chunks (one per category, per the deterministic-pull
idea) + the user's plain-English prompt + an explicit instruction to emit the
six `-- SECTION: X` markers in a fixed order. Dialect is fixed project-wide,
so it belongs in the system framing rather than as a variable slot.

Worth being explicit in the prompt that the LLM should **only reference
tables/columns present in the retrieved schema context** — this is the main
lever for reducing hallucinated column names, and it's the kind of grounding
instruction that's worth testing deliberately (e.g., ask for a procedure that
touches a table outside the retrieved set and see whether it invents one).

## Section marker contract

Something like:

```
-- SECTION: PRE_VALIDATION
...
-- SECTION: TEMP_TABLES
...
-- SECTION: BACKUP
...
-- SECTION: MAIN_SCRIPT
...
-- SECTION: POST_VALIDATION
...
-- SECTION: ROLLBACK_AUTO
...
-- SECTION: ROLLBACK_MANUAL
...
```

Fixing the order in the prompt (rather than letting the LLM choose) makes the
parser in `02-backend.md` much simpler and the output more predictable across
runs — probably worth enforcing rather than treating as a suggestion to the
model.

## Things worth testing once built

- How the model behaves when the prompt is ambiguous about which table it
  means (e.g., "update the order status" when there are multiple
  order-related tables) — does retrieval disambiguate, or does the prompt
  need an explicit "ask for clarification" fallback path?
- Whether temperature needs tuning — lower temperature is probably better
  here given the goal is consistent structural output rather than creative
  variation.
- PL/pgSQL fidelity — spot-check that the model consistently uses
  `BEGIN ... EXCEPTION ... END` for rollback (rather than drifting toward
  T-SQL's `TRY/CATCH`, which it will have seen far more of in training) —
  worth reinforcing with a concrete PL/pgSQL example in the retrieved pattern
  chunk rather than relying on the system framing alone.
