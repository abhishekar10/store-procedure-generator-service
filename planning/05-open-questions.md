# Open Questions

Things worth deciding before or during the build — not blockers, just flagged
so they don't get decided implicitly by whatever's easiest to code first.

- **Sample domain** for `reference.db` — e-commerce suggested in
  `03-db-and-rag.md`, but not committed.
- **Default dialect** — which of T-SQL / PL-pgSQL / PL-SQL is the default
  selection in the UI.
- **Pattern corpus authoring** — dialect-specific files per category vs. one
  dialect-agnostic description per category (trade-off noted in
  `03-db-and-rag.md`).
- **Retrieval strategy for patterns** — deterministic per-category pull vs.
  pure similarity search across the whole pattern corpus.
- **Regenerate/iterate loop** — one-shot generation per prompt, or a
  feedback loop where the user can ask for a revision against the same
  retrieved context.
- **Schema explorer in the frontend** — nice-to-have panel showing
  `reference.db` tables directly, separate from the retrieved-context panel.
- **Section-marker failure handling** — what the parser does if the LLM
  omits or mislabels a section.
