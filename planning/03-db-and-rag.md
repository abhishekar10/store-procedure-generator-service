# DB & RAG Corpus Notes

Suggestions only.

## Reference schema DB (`db/reference.db`, SQLite, metadata-only)

You'll need a sample business domain to seed this with. A few options, any of
which would work fine for demo purposes:

- E-commerce (customers, orders, order_items, products, inventory, payments)
- Banking-lite (accounts, transactions, customers, branches)
- HR/payroll (employees, departments, salaries, timesheets)

E-commerce is probably the easiest to make realistic-looking with FK chains
that justify multi-table stored procedures (e.g., "reduce inventory and log a
transaction when an order is placed" naturally touches 3+ tables), which gives
the demo more interesting retrieval to show off.

Suggested per-table metadata to store (beyond the raw DDL), since this is what
actually gets embedded for retrieval:
- Table name + one-line purpose
- Column name, type, nullable, and a short description
- FK relationships (table + column it references)
- Maybe 1 sample row, to help the LLM understand realistic value shapes

## Pattern corpus (`backend/app/sp_templates/`)

Suggested category breakdown (matches the six output sections):
- `pre_validation/` — existence checks, input sanity checks, permission checks
- `temp_tables/` — staging/temp table creation + cleanup patterns
- `backup/` — snapshotting affected rows before mutation
- `post_validation/` — row-count assertions, referential-integrity checks
- `rollback_auto/` — TRY/CATCH or exception-block patterns per dialect
- `rollback_manual/` — a separate manual-invocation rollback procedure

Since the dialect is a per-request parameter rather than a fixed engine,
consider whether each pattern file should have a version per dialect (e.g.
`rollback_auto/tsql.sql`, `rollback_auto/plpgsql.sql`) or whether one
dialect-agnostic description is embedded and the LLM is trusted to translate
syntax. Per-dialect files are more work to author but reduce the risk of the
LLM inventing incorrect syntax for a less common dialect.

## Chroma collections

Two collections keep the retrieval concerns cleanly separated:
- `schema_chunks` — metadata: `{table_name}`
- `pattern_templates` — metadata: `{category, dialect}`

Filtering by metadata (e.g., `category == "rollback_auto"` and
`dialect == selected_dialect`) rather than relying purely on similarity search
is probably more reliable for the pattern corpus, given the deterministic
per-category retrieval idea from `02-backend.md`.

## App-state DB (`backend/data/app.db`, SQLite)

Kept separate from `reference.db` so "the business schema being queried" and
"the app's own bookkeeping" don't mix. Suggested minimal table:

```
generations(id, prompt, dialect, full_script, sections_json,
            retrieved_context_json, created_at)
```

Simple enough to not need an ORM — direct `sqlite3` calls are probably fine at
this scale, though SQLAlchemy is a reasonable alternative if you'd rather have
schema migrations as the project grows.
