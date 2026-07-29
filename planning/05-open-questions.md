# Open Questions

Things worth deciding before or during the build — not blockers, just flagged
so they don't get decided implicitly by whatever's easiest to code first.

Resolved: sample domain (HR/payroll), SQL dialect (single, PL/pgSQL, no
selector), pattern corpus authoring (one file per category, no per-dialect
variants), retrieval strategy for patterns (deterministic per-category pull).
See `03-db-and-rag.md` and `03a-reference-schema-design.md`.

Still open:

- **Regenerate/iterate loop** — one-shot generation per prompt, or a
  feedback loop where the user can ask for a revision against the same
  retrieved context.
- **Schema explorer in the frontend** — nice-to-have panel showing
  `reference.db` tables directly, separate from the retrieved-context panel.
- **Section-marker failure handling** — what the parser does if the LLM
  omits or mislabels a section.
- **Payroll numeric realism** — how much effort to put into realistic sample
  data (tax/deduction amounts, salary bands) vs. placeholder values, given
  the DB is metadata-only and never executed against.
