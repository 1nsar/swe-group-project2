# swe-group-project2 — Collaborative Document Editor with AI Writing Assistant

Implementation for Assignment 2. A React + TipTap frontend and a FastAPI
backend, with JWT-based auth, real-time collaboration over WebSockets, and a
token-by-token streaming AI writing assistant.

## Ownership at a glance

| Area | Owner | Paths |
| --- | --- | --- |
| Auth, JWT lifecycle, permissions/sharing | Member 1 | (landing in `backend/routers/auth.py`, `backend/dependencies/auth.py`) |
| Document CRUD, rich-text editor, versioning, dashboard | Member 2 | `backend/routers/documents.py`, `backend/models/document.py`, `frontend/src/pages/*`, `frontend/src/components/{EditorToolbar,VersionHistory}.tsx` |
| Real-time collaboration (WebSocket), presence, AI panel, streaming, accept/reject, AI history, tests | Member 3 | `backend/routers/{collaboration,ai}.py`, `backend/services/*`, `backend/dependencies/ws_auth.py`, `backend/models/ai.py`, `backend/tests/*`, `frontend/src/hooks/useCollaboration.ts`, `frontend/src/components/{AIPanel,PresenceBar}.tsx`, `frontend/src/api/{ai,ws}.ts` |

## Quick start

```bash
cp .env.example .env        # fill in SECRET_KEY and (optionally) ANTHROPIC_API_KEY
./run.sh                    # starts backend on :8080 and frontend on :5174
```

Without an `ANTHROPIC_API_KEY`, the backend automatically falls back to a
deterministic **MockProvider** that streams tokens so the UI, tests, and demo
still work offline.

## Ports

| Service | URL |
| --- | --- |
| Backend HTTP + WebSocket | http://localhost:8080 |
| FastAPI auto-docs | http://localhost:8080/docs |
| Frontend | http://localhost:5174 |

The WebSocket lives on the same FastAPI server as the HTTP API (not a separate
port), so `VITE_WS_URL` should be `ws://localhost:8080` — see deviations below.

## How the pieces fit together

### Authentication

- Member 1 owns the full JWT lifecycle (register, login, refresh, validation).
- Members 2 and 3 rely on a thin `get_current_user` dependency that decodes
  the JWT. Member 2's HTTP stub and Member 3's WebSocket helper share the
  same `CurrentUser` shape, so swapping in Member 1's fully-verified decoder
  is a one-line change per file.
- Access tokens: 30 min. Refresh tokens: 7 days. Configurable via `.env`.

### Documents + versioning

- JSON-file storage in `backend/data/` (no database — baseline allows this).
- Auto-save: TipTap `onUpdate` → debounced `PUT /api/documents/{id}`.
- Versions: snapshot on create, on manual save, and before any restore.

### Real-time collaboration (Member 3)

- Endpoint: `ws://HOST/ws/documents/{id}?token=<JWT>`.
- Authentication: JWT passed as a query param (browsers don't allow custom
  WS handshake headers). Handshake is rejected with close code 1008 when no
  valid token is provided and `ALLOW_DEV_WS_FALLBACK=0`.
- Server state: `CollaborationManager` (`backend/services/collaboration_manager.py`)
  keeps an in-memory `Room` per document with the connected users, a
  monotonic `revision` counter, and the latest full-document snapshot.
- Concurrency model: **last-write-wins at the snapshot level**. Each accepted
  update bumps the revision; clients send their `last_seen_revision` on
  (re)connect and get a fresh `state` frame if they're behind. This is the
  Assignment 2 baseline — CRDTs/OT are noted as bonus.
- Lifecycle: initial connect → `state` frame → live `update`/`presence`/
  `typing` messages → reconnect with exponential backoff (500 ms → 8 s cap).
  Offline edits are buffered on the client and flushed on reconnect.
- AI output during collaboration: in-flight AI suggestions stay local to the
  invoking user. Nothing is sent over the realtime channel until the user
  clicks Accept — at which point the normal editor edit pathway fires and
  the updated content is broadcast to collaborators. This keeps half-formed
  generations out of other users' views.

### AI assistant (Member 3)

- Streaming transport: **SSE** (`text/event-stream`) via FastAPI's
  `StreamingResponse`. Chosen over WebSocket because AI is one-shot
  request/response, it reuses the HTTP `Authorization` header, and client
  cancellation maps naturally to `AbortController.abort()` on `fetch`.
- Provider abstraction (§3.4): `backend/services/llm_provider.py` defines an
  `LLMProvider` protocol. Two implementations ship: `AnthropicProvider`
  (real Claude API) and `MockProvider` (deterministic, no network). Factory
  selects via `LLM_PROVIDER` env. Swapping providers = one place.
- Prompt templates (§3.4): `backend/services/prompts.py`. Not hardcoded in
  router code. Can be further overridden without a code change by pointing
  `PROMPTS_OVERRIDE_PATH` at a JSON file.
- Context handling: selection is truncated at 6000 chars (configurable via
  `MAX_SELECTION_CHARS`) with a head/tail split so long docs degrade
  gracefully. A truncation note is appended so the model knows its context
  is partial.
- Cancellation (§3.2): `AbortController.abort()` on the client → `fetch`
  body closes → FastAPI `request.is_disconnected()` watcher flips an
  `asyncio.Event` → provider loop exits cleanly → partial output is
  displayed in the UI with a "cancelled" indicator.
- Suggestion UX (§3.3): generated text appears in a side panel (never
  inserted into the editor until the user clicks Accept). The user sees the
  original selection vs the suggestion, then picks Accept, Reject, or Edit.
  Acceptance triggers a single TipTap `insertContentAt` so native undo
  works (Ctrl/Cmd+Z reverts the AI change as one step).
- Interaction history (§3.5): every call is persisted as an
  `AIInteraction` record (input, prompt, model, status, token usage,
  created/completed/decided timestamps). A History tab in the AI panel
  lists all interactions for the document, most recent first.

### Baseline AI features shipped

1. **Rewrite / Rephrase** — with tone options (neutral/formal/friendly/concise/persuasive).
2. **Summarize** — with length options (short/medium/long/bullets).
3. *(Grammar fix + Custom Prompt are also wired up and selectable from the panel.)*

### Bonus features (Member 2)

4. **Partial AI suggestion acceptance (+2 pts)** — the diff view in the AI panel makes each inserted word/phrase clickable. Clicking toggles it to a rejected state (greyed out, strikethrough). An "Accept Selected" button appears whenever at least one insert is rejected, applying only the kept changes via a word-level LCS diff. "Accept All" preserves the original full-acceptance flow.

5. **Playwright E2E tests (+2 pts)** — `frontend/e2e/app.spec.ts` covers the full user journey in a real Chromium browser (6 tests, all passing):
   - Register → login
   - Create document → type → auto-save indicator
   - Save version → edit → open history → restore
   - Title change → triggers save → appears on dashboard
   - AI panel opens, shows compose UI, closes
   - Logout → redirects to `/login`

## Tests

Backend (`pytest`) — run from the repo root:

```bash
. .venv/bin/activate
pytest backend/tests -v
```

- `test_prompts.py`: template rendering, truncation, override file, unknown action.
- `test_llm_provider.py`: mock streams + cancellation; factory fallback rules.
- `test_ai_api.py`: end-to-end SSE happy path, auth, accept/reject, history isolation.
- `test_websocket.py`: unauth rejection, valid-token handshake, two-client update exchange, typing propagation, stale-client reconciliation.

Frontend unit (`vitest`) — run from `frontend/`:

```bash
cd frontend && npm install && npm test
```

- `AIPanel.test.tsx`: renders controls, disables Generate without selection, shows streaming tokens, accept/reject wiring.

Frontend E2E (`playwright`) — requires backend + frontend both running:

```bash
cd frontend && npm run test:e2e
```

- `e2e/app.spec.ts`: 6 end-to-end tests covering register/login, document creation, auto-save, version restore, AI panel, and logout.

## Architecture deviations from Assignment 1

See [`DEVIATIONS.md`](./DEVIATIONS.md).

## Demo flow (5 min)

1. Register + login (protected routes — `/documents/:id` redirects to dashboard when logged out).
2. Create a document, type, watch auto-save, switch headings, add a list.
3. Share the doc with a second user as editor.
4. Open the same doc in a second browser window (incognito/second login) — type in one, watch it appear in the other within a fraction of a second. Show the presence chip turn green and the typing dot pulse.
5. Select a paragraph → open **AI** panel → pick **Rewrite**, tone = **formal** → click Generate. Show tokens streaming in. Click **Stop** mid-stream to demonstrate cancellation. Re-generate → **Accept** → show the paragraph replaced and Ctrl+Z restoring the original (one-step undo).
6. Switch to **Summarize** with length = **bullets** on the full paragraph; accept.
7. Open **History** tab — show the two interactions with status chips.
8. Save Version → edit something → open Version History → restore.
