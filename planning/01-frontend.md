# Frontend Notes (React + JSX)

Suggestions only — the layout below is one reasonable way to slice it, not a
required structure.

**Superseded by an explicit direction change**: the tabbed section-viewer
described below was the original sketch, but the actual build is a
Claude/ChatGPT-style chat UI instead — a single scrolling thread with
follow-up messages that revise the previous script, a regenerate button, and
streamed responses. See `frontend/README.md` for what's actually there; the
notes below are kept for the design reasoning that still applies (retrieved-
context transparency, download-as-.sql, etc.) but the concrete component
list is out of date.

## Suggested screens/components

- **Prompt panel** — free-text input for the plain-English ask. No dialect
  selector needed — the project standardizes on a single dialect (PL/pgSQL),
  so this panel is just the prompt box.
- **Section tabs** — one tab per parsed section (Pre-Validation, Temp Tables,
  Backup, Main Script, Post-Validation, Rollback (Auto), Rollback (Manual)),
  plus a "Full Script" tab that's just the raw concatenated output for
  copy/download. A code viewer with SQL syntax highlighting will make this feel
  much more finished than a plain `<pre>` — worth picking a lightweight
  highlighter rather than pulling in something heavy.
- **Retrieved-context panel** — this is arguably the most important UI piece
  for demonstrating RAG skill. Show which schema chunks (table names) and
  which pattern chunks (categories) were retrieved for this specific
  generation. A simple collapsible list with a similarity score per chunk
  would make the retrieval step legible rather than a black box.
- **History sidebar** — past prompts + generations pulled from
  `GET /history`. Clicking one could re-populate the section tabs without
  re-calling the LLM.

## State management

Given the app is small (one main flow: prompt → generate → view), you probably
don't need Redux/Zustand — component-local state plus a thin API client module
should be enough. Only reach for a state library if history/session state
starts getting passed through too many layers.

## Data contract with the backend

This is now the actual implemented shape (`backend/app/main.py`), not a
sketch:

```
POST /generate
  body: { prompt: string }
  response: {
    generation_id: string,
    prompt: string,
    full_script: string,
    sections: { pre_validation, temp_tables, backup, main_script,
                post_validation, rollback_auto, rollback_manual },
    retrieved_context: {
      schema_chunks:  [{ id, text, metadata: { table_name }, score }],
      pattern_chunks: [{ id, text, metadata: { category }, score: null }]
    }
  }

GET /schema     -> { tables: [{ table_name, purpose, columns: [...] }] }
GET /history    -> { generations: [{ id, prompt, created_at }] }
GET /history/{id} -> full generation record (404 if missing)
POST /ingest    -> re-embeds schema + pattern corpus into Chroma
```

`retrieved_context` ended up as two separate lists (schema vs. pattern)
rather than one flat tagged list — it maps directly onto how retrieval
actually works (similarity search vs. deterministic per-category pull), and
the frontend can render each in its own labeled group without needing to
filter by `type`.

## Things worth deciding before building

- Does the retrieved-context panel need live click-through (e.g., clicking a
  retrieved table opens its schema), or is a static list enough for the demo?
- Is there a "regenerate with feedback" loop, or is generation strictly
  one-shot per prompt? A regenerate loop would show iterative RAG well, but
  adds real scope.
