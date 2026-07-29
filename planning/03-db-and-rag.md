# DB & RAG Corpus Notes

Suggestions only, except where marked Decided.

## Reference schema DB (`db/reference.db`, SQLite, metadata-only)

**Decided: HR / payroll domain.** Table-level detail is in
`03a-reference-schema-design.md`. Payroll processing naturally chains
employees → timesheets → salary history → deductions → payroll entries,
which gives multi-table stored-procedure prompts real FK depth to justify
pre-validation, backup, and rollback sections — not just a CRUD update.

Suggested per-table metadata to store (beyond the raw DDL), since this is what
actually gets embedded for retrieval:
- Table name + one-line purpose
- Column name, type, nullable, and a short description
- FK relationships (table + column it references)
- Maybe 1 sample row, to help the LLM understand realistic value shapes

## Dialect

**Decided: single dialect, PL/pgSQL, for the whole project.** No per-request
dialect parameter, no dialect selector in the UI. This was a deliberate
simplification — enterprises typically standardize on one engine per
database, so a per-request dialect switch was modeling a flexibility real
deployments don't usually need. This removes an entire axis of complexity
from the pattern corpus, retrieval, and prompt assembly.

## Pattern corpus (`backend/app/sp_templates/`)

Suggested category breakdown (matches the six output sections), now **one
file per category** rather than per-dialect variants, since the dialect is
fixed:
- `pre_validation.sql` — existence checks, input sanity checks, permission checks
- `temp_tables.sql` — staging/temp table creation + cleanup patterns
- `backup.sql` — snapshotting affected rows before mutation
- `post_validation.sql` — row-count assertions, referential-integrity checks
- `rollback_auto.sql` — `BEGIN ... EXCEPTION ... END` pattern (PL/pgSQL)
- `rollback_manual.sql` — a separate manually-invocable rollback procedure

Worth authoring these against the actual HR/payroll schema (e.g., the backup
pattern snapshots `payroll_entries` before a recalculation) rather than
generic examples — the more concretely these tie to real tables, the more
useful they are as few-shot grounding.

## Chroma collections

Two collections keep the retrieval concerns cleanly separated:
- `schema_chunks` — metadata: `{table_name}`
- `pattern_templates` — metadata: `{category}` (no `dialect` key needed now
  that dialect is fixed project-wide)

**Decided: pattern retrieval is a deterministic per-category pull** — always
fetch exactly one chunk per category (filtered by `category` metadata) rather
than similarity-searching the pattern corpus. Guarantees all six sections
have grounding regardless of how the user's prompt is worded. Schema
retrieval stays similarity-based, since which tables are relevant genuinely
varies per prompt.

## App-state DB (`backend/data/app.db`, SQLite)

Kept separate from `reference.db` so "the business schema being queried" and
"the app's own bookkeeping" don't mix. Suggested minimal table:

```
generations(id, prompt, full_script, sections_json,
            retrieved_context_json, created_at)
```

(No `dialect` column needed now that it's fixed.) Simple enough to not need
an ORM — direct `sqlite3` calls are probably fine at this scale, though
SQLAlchemy is a reasonable alternative if you'd rather have schema migrations
as the project grows.
