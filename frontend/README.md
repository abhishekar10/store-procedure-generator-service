# Frontend

React + Vite chat UI for the stored procedure generator. Talks to the
backend's `/v1/generate/stream` (SSE) -- see `../backend/README.md` for the
API. The service is stateless, so conversation history lives here, in the
browser's localStorage.

## Layout

```
frontend/
  src/
    api.js                       backend client: streamGenerate
    conversationStore.js         conversation history in localStorage + the request-body shape
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

Node 22 (the version the image builds with):

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173
npm run lint       # oxlint
npm run build      # static files in dist/
```

The backend is expected at `http://127.0.0.1:8000` by default; set
`VITE_API_BASE_URL` (e.g. in a gitignored `.env.local`) if it runs
elsewhere. Vite inlines it at build time, so in the container image it is a
build argument, not a runtime setting. The backend must allow the UI's
origin: `CORS_ALLOW_ORIGINS=http://localhost:5173` (compose sets this).

With `docker compose up --build` from the repo root the UI is built and
served by nginx on :5173 instead; no local Node install is needed.

## How the chat maps to the backend

- **History is the browser's.** Conversations are saved in localStorage
  under `sp-generator.conversations.v1` (`src/conversationStore.js`). Every
  storage access is guarded: in a private window, with blocked site data, or
  when the quota is full the app keeps working and history simply isn't
  saved. If the quota is exceeded, the oldest conversations are dropped until
  the rest fit. Clearing site data clears the history.
- **Every request carries the conversation.** Sending calls
  `/v1/generate/stream` with `messages`: every earlier turn plus the new
  request, reduced to what the service accepts (`toApiMessages`: user text,
  earlier scripts, earlier rejection reasons). Follow-ups therefore read as
  revisions of the previous script, not fresh unrelated ones.
- A new conversation is created in the browser the moment the first message
  is sent, titled from that message; it shows in the sidebar immediately.
- A reply joins its conversation only when it is `done` or `rejected`. A
  failed or half-streamed reply is shown as an error but never saved, so it
  is never sent back to the service as history.
- **Regenerate** resends the conversation without its last answer. The old
  answer stays hidden while the new one streams and is replaced when it
  finishes; if regeneration fails, the old answer is shown again.
- The thinking indicator reflects the backend's real `status` SSE events
  (`checking_request` / `retrieving_context` / `drafting_script` /
  `validating_script`, see `src/thinkingStages.js`), not a generic spinner.
  `drafting_script` is the long step -- typically 10-20s -- so its
  sub-phrases cycle client-side to keep the wait legible.
- **Off-topic requests are rejected before generation runs.** The backend
  gates every request through a small classification call; a rejection comes
  back as a `rejected` SSE event and renders as an info box explaining why,
  instead of a code block. It is saved with `kind: "rejected"` and sent back
  as such, so later turns know why nothing was generated.
- Deleting a conversation (trash icon on hover) confirms via `window.confirm`
  and removes it from localStorage; if the deleted thread was active, the
  chat resets to the empty state.
- **Download** and **Copy** are pure client-side (`Blob` + temporary
  `<a download>`, `navigator.clipboard`) -- no backend round-trip.
- The retrieved-context panel renders `retrieved_context.schema_chunks` /
  `.pattern_chunks` directly from the `done` event -- it's the same data the
  backend used to ground that specific generation, not a separate lookup. It
  is saved with the message; `sections` is not, because the seven section
  markers are already visible in the script itself.

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
