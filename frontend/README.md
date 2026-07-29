# Frontend

React + Vite chat UI for the stored procedure generator. Talks to the
backend's `/generate/stream` (SSE) and `/conversations` endpoints -- see
`../backend/README.md` for the API.

## Layout

```
frontend/
  src/
    api.js                       backend client: listConversations, getConversation, deleteConversation, streamGenerate
    utils.js                     download-as-.sql, slugify, relative time formatting
    starterPrompts.js            the 4 candidate prompts shown on an empty chat
    thinkingStages.js            backend status-stage labels + drafting sub-phrases
    styles.css                   design system (CSS custom properties, light/dark, responsive)
    App.jsx                      top-level state: conversations, active thread, streaming, mobile drawer
    components/
      Sidebar.jsx                 conversation list + new chat + delete + theme toggle
      ChatWindow.jsx                message list, auto-scroll, empty state
      MessageBubble.jsx              renders one user/assistant/rejected/error message
      CodeBlock.jsx                    syntax-highlighted SQL + copy button
      ValidityBadge.jsx                 pass/fail pill from the backend's sql_valid check
      RetrievedContextPanel.jsx          expandable pills + full text of retrieved chunks
      StarterPrompts.jsx                  clickable example prompts
      ChatInput.jsx                        auto-resizing textarea, Enter to send
      ThemeToggle.jsx
```

## Setup

```bash
cd "05 - RAG Project/frontend"
npm install
npm run dev
```

Runs at http://localhost:5173. The backend is expected at
`http://127.0.0.1:8000` by default; override with a `VITE_API_BASE_URL` env
var (e.g. in a `.env.local`) if it's running elsewhere.

## How the chat maps to the backend

- Sending a message with no active conversation calls `/generate/stream`
  with `conversation_id: null`; the `start` SSE event returns the new id,
  which is then reused for every later message in that thread.
- Follow-up messages are just another `/generate/stream` call with the same
  `conversation_id` -- the backend threads in the full prior conversation, so
  the result reads as a revision, not a fresh unrelated script.
- **Regenerate** calls `/generate/stream` with `is_regenerate: true` and no
  new prompt text -- the backend reuses the last user message and appends a
  new assistant message rather than duplicating the user turn.
- The thinking indicator reflects the backend's real `status` SSE events
  (`checking_request` / `retrieving_context` / `drafting_script` /
  `validating_script`, see `src/thinkingStages.js`), not a generic spinner.
  `drafting_script` is the long step -- Gemini's reasoning time before the
  first token can be 30-60s -- so its sub-phrases cycle client-side to keep
  the wait legible.
- **Off-topic requests are rejected before generation runs.** The backend
  gates every request through a small classification call; a rejection comes
  back as a `rejected` SSE event (or `rejected: true` from `/generate`) and
  renders as an info box explaining why, instead of a code block. Persisted
  history messages carry `kind: "rejected"` to distinguish them from real
  generations on reload.
- The sidebar refreshes on the `start` event, not just `done` -- a new
  conversation shows up as soon as the request is sent, before the response
  finishes.
- Deleting a conversation (trash icon on hover) confirms via `window.confirm`
  then calls `DELETE /conversations/{id}`; if the deleted thread was active,
  the chat resets to the empty state.
- **Download** and **Copy** are pure client-side (`Blob` + temporary
  `<a download>`, `navigator.clipboard`) -- no backend round-trip.
- The retrieved-context panel renders `retrieved_context.schema_chunks` /
  `.pattern_chunks` directly from the `done` event -- it's the same data the
  backend used to ground that specific generation, not a separate lookup.

## Responsive layout

Below 860px wide, the sidebar becomes an off-canvas drawer (`transform:
translateX`) instead of a permanent column: a hamburger button appears in a
mobile top bar, tapping it slides the drawer in over a dimmed backdrop, and
tapping the backdrop (or selecting/starting a chat) closes it again. Below
560px the starter-prompt grid drops from two columns to one. Code blocks
already scroll horizontally within their own bounds at any width (a `pre`
element inside a flex column needs `min-width: 0` on every ancestor or long
SQL lines blow out the page instead of scrolling internally -- worth knowing
if you add another wide-content component).
