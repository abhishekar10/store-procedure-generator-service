# Frontend Notes (React + JSX)

Suggestions only — the layout below is one reasonable way to slice it, not a
required structure.

## Suggested screens/components

- **Prompt panel** — free-text input for the plain-English ask, plus a dialect
  dropdown (T-SQL / PL-pgSQL / PL-SQL). Consider keeping the dropdown's default
  sticky per session so repeat testing isn't tedious.
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

## Suggested data contract with the backend

Not final — just illustrating the shape so frontend and backend design against
the same thing:

```
POST /generate
  body: { prompt: string, dialect: "tsql" | "plpgsql" | "plsql" }
  response: {
    sections: { pre_validation, temp_tables, backup, main_script,
                post_validation, rollback_auto, rollback_manual },
    full_script: string,
    retrieved_context: [{ type: "schema" | "pattern", label: string, score: number }],
    generation_id: string
  }
```

## Things worth deciding before building

- Does the retrieved-context panel need live click-through (e.g., clicking a
  retrieved table opens its schema), or is a static list enough for the demo?
- Is there a "regenerate with feedback" loop, or is generation strictly
  one-shot per prompt? A regenerate loop would show iterative RAG well, but
  adds real scope.
