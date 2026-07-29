# GenAI / Prompting Notes (Gemini)

Suggestions only.

## Models

- Generation: `gemini-2.0-flash` is probably enough for this task and keeps
  latency low for a demo; `gemini-1.5-pro` is a fallback if section quality
  turns out to need more reasoning depth.
- Embeddings: `text-embedding-004` (via `langchain-google-genai`) for both the
  schema corpus and the pattern corpus, so everything stays on one provider —
  consistent with what's already set up from project 04.

## Suggested prompt shape

Roughly: system framing ("you are generating a production-grade stored
procedure following these six sections...") + retrieved schema chunks +
retrieved pattern chunks (one per category, per the deterministic-pull idea)
+ the user's plain-English prompt + the selected dialect + an explicit
instruction to emit the six `-- SECTION: X` markers in a fixed order.

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
- Dialect fidelity — spot-check that PL/pgSQL vs T-SQL syntax differences
  (e.g., `BEGIN...EXCEPTION` vs `BEGIN TRY...BEGIN CATCH`) are actually
  respected rather than the model defaulting to whichever dialect it has seen
  most in training.
