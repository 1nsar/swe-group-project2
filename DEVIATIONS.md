# Architecture Deviations from Assignment 1

This is the penalty-free deviation log required by Assignment 2's grading
rubric and submission format. Each item below explains *what changed*, *why*,
and *whether the change was an improvement, a conscious compromise, or a
platform-level constraint*. Section numbers refer to the Assignment 1 design
document (Merged_Styled.docx).

## Member 3 scope: real-time collaboration, AI, streaming, tests

### 1. Concurrency model: last-write-wins, not Yjs CRDT (violates ADR-001)

- **Design (A1):** §2.5 ADR-001 mandates Yjs (Y.Doc, Y.Text) as the conflict
  resolution layer, chosen precisely to avoid the "merge conflicts or lost
  edits" failure mode of LWW. §1.2 FR-RT-01 also calls out concurrent editing
  without conflicts as a functional requirement, and NFR-C-02 (Assignment 1
  §1.3) lists the target of "merged without conflicts in < 1s".
- **Implementation:** `backend/services/collab.py` keeps a single authoritative
  TipTap JSON snapshot per document with a monotonic `revision` integer. Each
  client `update` replaces the full document; the server bumps the revision
  and rebroadcasts. Clients apply any update whose revision is higher than
  their last-seen.
- **Why:** shipping a production-grade Yjs integration (including `y-websocket`
  server, awareness protocol migration, and a replacement for our TipTap JSON
  snapshots) is genuinely a multi-week effort and Assignment 2 explicitly
  accepts LWW as the baseline and lists OT/CRDT as bonus-tier. Doing it
  half-way would have taken time from AI integration, testing, privacy
  compliance, and permissions — all of which have firm A1 requirements.
- **Verdict:** conscious compromise. The snapshot protocol is intentionally
  shaped so a future Yjs migration is additive: the `update` message already
  carries a revision and a full content payload, so it can be replaced by a
  Yjs update binary with the same wire-level envelope. Awareness (cursors) is
  also already separated onto its own message type (`cursor`), matching the
  Yjs awareness protocol's separation.

### 2. AI transport: SSE, not a Redis+BullMQ queue behind WebSockets (violates ADR-002)

- **Design (A1):** §2.5 ADR-002 mandates an async job queue (Redis + BullMQ)
  behind a dedicated FastAPI AI service, with results pushed over the existing
  WebSocket channel as `ai:pending` / `ai:result` / `ai:error` events so peers
  can see each other's AI activity (§2.2 AI Integration Design).
- **Implementation:** AI streaming is a direct HTTP SSE endpoint at
  `POST /api/ai/stream` on the *same* FastAPI process as the rest of the
  backend. Events are named `meta` / `pending` / `token` / `done` / `cancel`
  / `error` on the stream, not broadcast to collaborators.
- **Why:**
  - The queue+worker architecture in ADR-002 is valuable when the AI service
    must absorb back-pressure and scale out horizontally. For a 3-person
    submission running on a single machine, the queue adds operational
    complexity (Redis + worker process + monitoring) without changing user-
    visible behaviour, and makes the integration tests much harder.
  - SSE gives us a single TCP stream per request with no framing work, reuses
    the HTTP `Authorization` header, and maps cancellation directly to
    `AbortController.abort()` on the client plus FastAPI's
    `request.is_disconnected()` on the server. Those two points together
    satisfy §2.2's "user can cancel a request" and the NFR-P-03 latency budget
    without a queue.
  - The SSE event vocabulary was deliberately chosen to mirror §2.2's
    `ai:pending` / `ai:result` / `ai:error` names, so a future migration to
    the ADR-002 shape is a mechanical rename rather than a re-architecture.
- **Verdict:** conscious compromise. Functional parity for a single-node
  deployment; the queue layer is the right decision at scale but not at this
  scale.

### 3. AI activity is not broadcast to peers (partial deviation from §2.2)

- **Design (A1):** §2.2 says collaborators should see `ai:pending` /
  `ai:result` events so they know when a peer is running an AI action.
- **Implementation:** AI events are delivered only to the initiating user.
  Peers see the edit via the normal `update` broadcast *after* the user
  clicks Accept.
- **Why:** broadcasting AI-in-progress from the backend requires coupling the
  AI router to the collab hub, which in turn assumes ADR-002's queue so the
  AI service can publish to the collab service. Without the queue, this
  cross-module coupling gets ugly fast. Keeping the events local also matches
  the UX the grading rubric seems to weight more highly — privacy-aware
  history + accept/reject trail — and avoids leaking an unaccepted suggestion
  into another collaborator's view.
- **Verdict:** conservative compromise. Trivially added once ADR-002 lands:
  the `done` event on the AI service side can publish an `ai:result` to Redis
  and the collab hub re-emits it.

### 4. No separate AI microservice (violates §2.3 / ADR-002)

- **Design (A1):** §2.3 C4 container diagram shows a dedicated FastAPI "AI
  Service" alongside the main backend. ADR-002 reinforces this split.
- **Implementation:** AI endpoints live inside the main FastAPI app at
  `backend/routers/ai.py`. The LLM provider abstraction
  (`backend/services/llm_provider.py`) cleanly isolates the vendor-specific
  code, but the HTTP surface is a single-process router.
- **Why:** no deployment-level benefit on a single machine; two FastAPI
  processes would only add startup time and a second port to juggle.
- **Verdict:** conscious compromise. The clean `LLMProvider` protocol boundary
  means the AI router can be lifted into its own service with no code
  changes — only an ingress config change.

### 5. WebSocket authentication via query-param JWT, not `Authorization` header (platform constraint)

- **Design (A1):** §2.4 sequence shows `Authorization: Bearer <token>` on the
  WebSocket handshake.
- **Implementation:** `/ws/documents/{id}?token=<jwt>`. The handshake decodes
  the query-param token identically to the HTTP path; server-side auth is
  unchanged.
- **Why:** browsers do **not** allow custom headers on the `WebSocket`
  constructor. This is a hard W3C-level constraint, not a design choice.
  The alternatives (Sec-WebSocket-Protocol abuse, cookie-based auth with
  separate CSRF mitigation) are both worse from a security-review standpoint.
- **Verdict:** forced platform compromise. Mitigations: tokens are short-
  lived, never echoed in responses, and the dev-only fallback flag
  `ALLOW_DEV_WS_FALLBACK` is off in production-style builds.

### 6. Baseline LLM `MockProvider` shipped as first-class (improvement over §2.2)

- **Design (A1):** §2.2 shows a provider abstraction with Anthropic as the
  only concrete implementation.
- **Implementation:** `LLMProvider` protocol + `AnthropicProvider` +
  `MockProvider`. The mock streams deterministic tokens and is auto-selected
  when `ANTHROPIC_API_KEY` is empty.
- **Why:** the mock makes the full test suite deterministic and network-free
  (required for CI on sandboxed runners), lets the team demo without burning
  API credits, and actually exercises the "abstract behind an interface"
  requirement by having two distinct implementations behind it.
- **Verdict:** improvement. No functional regression; the mock is not used
  when a real key is configured.

### 7. `custom` and `grammar` actions added beyond the A1 spec (improvement)

- **Design (A1):** §1.2 FR-AI-01..05 names Rewrite, Summarize, Translate, and
  (implicitly) a generic instruction path.
- **Implementation:** `actions = {"rewrite", "summarize", "translate",
  "grammar", "custom"}` in `backend/services/prompts.py`. `custom` accepts a
  user-supplied instruction; `grammar` is a specialized light-edit template.
- **Why:** `custom` is the natural landing place for the "instruction" field
  mentioned in the §2.2 sequence, and `grammar` was a common enough request in
  early testing that a dedicated template (which preserves meaning and tone)
  outperforms the generic `custom` path.
- **Verdict:** improvement, additive only; core FR-AI actions are preserved.

### 8. AI interaction history scoped to the caller (conservative compromise vs. §1.2 FR-AI-06)

- **Design (A1):** §1.2 FR-AI-06 implies per-document history visible to
  collaborators with document access.
- **Implementation:** `GET /api/ai/history/{doc_id}` returns only the caller's
  own interactions (plus the privacy-compliant fields — see deviation 9).
- **Why:**
  - Showing Alice's in-flight or rejected suggestion to Bob before Alice has
    decided is a privacy mis-feature — it would leak a user's drafting
    thought-process to their collaborators.
  - Member 1's permission table is landing in parallel; the AI router is
    deliberately decoupled from it so that AI code doesn't have to re-query
    the permission store on every history read.
- **Verdict:** conservative compromise. Once the permissions module is final,
  the filter can expand to "interactions visible to collaborators with
  document read access" in a single query change in
  `backend/routers/ai.py::history`. The privacy-compliant schema (deviation 9)
  guarantees no raw text leaks even if the scope is widened.

### 9. Privacy: `AIInteraction` stores hashes and lengths, not raw text (§2.2 AI Integration Design)

- **Design (A1):** §2.2 explicitly requires privacy-preserving AI interaction
  logging: prompt hash + token count, no raw prompt or output text, 30-day
  retention. §1.3 NFR-SEC-04 reinforces "no plaintext user content in logs".
- **Implementation:** `backend/models/ai.py::AIInteraction` stores:
  - `prompt_hash` (SHA-256 of the canonicalized rendered messages)
  - `selection_length` (character count of the selection)
  - `output_length` (character count of the streamed output)
  - `instruction_hash` (SHA-256 of any user-supplied custom instruction)
  - `input_tokens`, `output_tokens`
  - `retention_expires_at` = `created_at + 30 days`
  - `target_language` (for translate — needed for audit/UX, not sensitive)
  - `action`, `status`, `accepted`, `applied_length`
  
  The fields `input_text`, `prompt_used`, `output_text` from the first-pass
  implementation have been **removed**. The history endpoint filters out any
  record where `retention_expires_at <= now`.
- **Why:** direct §2.2 compliance. Also a defense-in-depth measure for
  NFR-SEC-04 — even if the audit database is exfiltrated, attackers cannot
  reconstruct user content.
- **Verdict:** improvement (brought into full compliance).

### 10. AI context split into three independent buckets (aligns with §2.2)

- **Design (A1):** §2.2 specifies that the AI request carries
  selection + surrounding context, with independent budgets so a large
  selection does not starve the surrounding context.
- **Implementation:** `PromptContext` has `context_before`, `context_after`,
  and `selection`, each with its own env-overridable budget
  (`MAX_SELECTION_CHARS=6000`, `MAX_CONTEXT_BEFORE_CHARS=2000`,
  `MAX_CONTEXT_AFTER_CHARS=800`). Each bucket is truncated independently:
  `context_before` is head-truncated (keeps the text closest to the
  selection), `context_after` is tail-truncated (same rationale), `selection`
  is middle-truncated.
  The frontend computes the buckets in `Editor.tsx::onSelectionUpdate` using
  `state.doc.textBetween` with `max(1, from - 2000)` / `min(docSize, to +
  800)` and passes them via the `AIPanel` props.
- **Verdict:** improvement (brought into full compliance).

### 11. Role-based AI gating (aligns with §1.2 FR-USER-04 and §2.4 permission model)

- **Design (A1):** §1.2 FR-USER-04 defines Owner / Editor / Commenter /
  Viewer roles; §2.4 notes Commenters can only propose changes and Viewers
  have read-only access. The AI endpoints must honour those roles.
- **Implementation:** `backend/services/ai_authz.py::decide(document,
  user_id, action, permissions)` returns an `AccessDecision(allowed, role,
  reason)`. Rules:
  - Viewer: denied on all AI actions.
  - Commenter: allowed on `summarize` and `translate` only (read-style
    actions that do not replace document text); denied on `rewrite`,
    `grammar`, `custom`.
  - Editor, Owner: allowed on all actions.
  - Unknown role (defensive): treated as viewer.
  - If `document.ai_enabled == False`: all AI actions denied regardless of
    role.
  The AI router returns HTTP 403 with a structured body
  `{code: "AI_ACCESS_DENIED", reason, role}` for denials, so the frontend
  can render a role-specific message without parsing English.
- **Verdict:** improvement (brought into full compliance).

### 12. Per-user monthly AI token quotas (aligns with §1.3 NFR-C-04 and §2.2 cost controls)

- **Design (A1):** §1.3 NFR-C-04 requires cost controls for the AI service;
  §2.2 specifies 80% warning + 100% suspend thresholds with monthly reset.
- **Implementation:** `backend/services/quota.py`:
  - `DEFAULT_MONTHLY_LIMIT = 200_000` tokens (env-overridable).
  - `peek(user_id)`, `check(user_id)` (raises `QuotaExceeded` at ≥100%),
    `record(user_id, tokens)`.
  - `QuotaSnapshot.warning == True` once usage ≥ 80%; `QuotaSnapshot.fraction`
    is exposed to the frontend.
  - Reset is computed as "first day of next month, UTC".
  - `QuotaExceeded.to_payload()` returns
    `{code: "AI_QUOTA_EXCEEDED", used, limit, reset_at}`.
  - Router returns HTTP 429 with the payload when `check` fails.
  - `meta` and `done` SSE events both carry the current `QuotaSnapshot` so
    the panel can update its banner live.
  - Frontend `AIPanel` shows an amber banner at ≥80% and a red banner at
    100% with the reset date.
- **Verdict:** improvement (brought into full compliance).

### 13. Diff view: side-by-side original vs. suggestion in the AI panel (§2.5 ADR-004)

- **Design (A1):** §2.5 ADR-004 chooses a side-panel diff for AI review
  (over inline tracked-changes), with original on the left and suggestion
  on the right.
- **Implementation:** a zero-dependency word-level LCS diff in
  `frontend/src/utils/wordDiff.ts` produces `{op: equal|insert|delete, text}`
  parts. `AIPanel.tsx::DiffView` renders them in a two-column grid: the left
  column shows `equal` + `delete` parts, the right column shows `equal` +
  `insert` parts. CSS (`index.css`) provides `.diff-eq`, `.diff-del` (red
  strikethrough), `.diff-ins` (green highlight).
- **Why (no external diff library):** the sandbox has proxy restrictions on
  npm installs, and a word-level LCS is ~60 lines and fast enough for the
  selection-sized inputs we diff. If a stronger diff (e.g., `diff-match-
  patch`) becomes available later, the `DiffPart` shape is a superset of
  what those libraries return, so swapping is trivial.
- **Verdict:** improvement (brought into full compliance, dependency-free).

### 14. Remote cursors rendered in the editor (§1.2 FR-RT-03)

- **Design (A1):** §1.2 FR-RT-03 Cursor/Editing Position Visibility —
  collaborators must see where each other are editing.
- **Implementation:**
  - `frontend/src/editor/RemoteCursors.ts` — a TipTap `Extension` that
    installs a ProseMirror plugin. The plugin state holds a `DecorationSet`:
    - `Decoration.widget(head, ...)` for the caret (a 2px vertical bar with
      a coloured username label).
    - `Decoration.inline(from, to, ...)` for the selection range when
      anchor ≠ head.
  - Colours are hashed from `user_id` (deterministic HSL) so they stay
    stable across reconnects — matches the avatar palette in `PresenceBar`.
  - Positions are clamped to `doc.content.size` so a stale cursor cannot
    throw inside ProseMirror after a local edit.
  - `useCollaboration` adds a new `cursor` WebSocket message type with
    150ms throttle + coalescing on send, and a 30-second stale sweep on
    receive. Cursors for users no longer in presence are dropped.
  - `Editor.tsx::onSelectionUpdate` calls `sendCursor(from, to)`; a `useEffect`
    pushes the remote-cursor list (filtered to exclude our own `user_id`)
    into the extension via `editor.commands.setRemoteCursors(...)`.
- **Verdict:** improvement (brought into full compliance).

### 15. Translate action with target-language parameter (§1.2 FR-AI-03)

- **Design (A1):** §1.2 FR-AI-03 Translate — user selects target language.
- **Implementation:** `Action` Literal now includes `"translate"`;
  `AIStreamRequest.target_language` is required for `action=="translate"`
  and validated at the router (422 if missing). `backend/services/prompts.py`
  has a dedicated `translate` template that substitutes `{target_language}`
  and instructs the model to preserve meaning/tone. The frontend `AIPanel`
  shows a language dropdown (English/Spanish/French/German/Italian/
  Portuguese/Japanese/Mandarin) when action is `translate`, and threads the
  selected language through `streamAI`.
- **Verdict:** improvement (brought into full compliance).

### 16. Partial / sentence-level Accept not implemented (§2.2 stretch)

- **Design (A1):** §2.2 mentions a future ability to accept only part of an
  AI suggestion sentence-by-sentence.
- **Implementation:** the AI panel's Accept button applies the full streamed
  output via `editor.chain().insertContent(text).run()`. Partial accept is
  not implemented.
- **Why:** partial accept requires either a custom ProseMirror plugin that
  wraps the inserted suggestion in track-changes marks and exposes a
  "confirm this sentence" UI, or a server-side diff model that the client
  can walk. Both are significant work and §2.2 flags this as a stretch goal.
- **Verdict:** conscious compromise. The diff view (deviation 13) already
  gives the user segment-level visibility; partial accept is the natural
  next step when time allows.

### 17. Restructure / long-form rewrite not implemented (§1.2 FR-AI-04)

- **Design (A1):** §1.2 FR-AI-04 Restructure — reorganize paragraphs, headings,
  or whole sections.
- **Implementation:** not in v1. `rewrite` covers sentence- and paragraph-
  level rewording; restructure would require streaming a model response that
  spans the entire document and a plan to apply multi-block edits atomically.
- **Why:** restructure also needs an interaction pattern very different from
  the selection-based panel (it operates on the whole document, not a
  highlight), and the grading rubric weights the primary actions (rewrite,
  summarize, translate) more heavily.
- **Verdict:** scope cut, documented.

### 18. WebSocket port (8001 → 8080) — fix, not a design change

- **Design (A1) / teammate defaults:** `.env.example` had
  `VITE_WS_URL=ws://localhost:8001`.
- **Implementation:** `ws://localhost:8080`, same port as the FastAPI server.
- **Why:** FastAPI serves HTTP and WebSocket on the same port — a second port
  would require a second process or a reverse proxy. The old value was a
  config leftover, not an intentional design.
- **Verdict:** configuration fix.

## Summary of verdict categories

- **Improvements beyond / into compliance with A1** (6, 7, 9, 10, 11, 12, 13,
  14, 15): privacy-compliant logging, context budgets, role gating, quotas,
  diff view, remote cursors, translate, mock provider, `custom`/`grammar`.
- **Conscious compromises with a documented upgrade path** (1, 2, 3, 4, 8,
  16, 17): LWW baseline, direct SSE, no peer AI broadcast, single-process AI
  router, per-user history scope, no partial accept, no restructure.
- **Forced platform constraints** (5): query-param JWT on WebSocket.
- **Configuration fixes** (18): WebSocket port.

## Member 2 scope: documents, editor, versioning, dashboard

### 19. Backend language: Node.js/Express → FastAPI (forced by Assignment 2)

- **Design (A1):** §2.2 C4 container diagram shows the backend API as
  "Node.js / Express".
- **Implementation:** FastAPI (Python). All REST document endpoints live in
  `backend/routers/documents.py`.
- **Why:** Assignment 2 mandates FastAPI as a technology constraint. This is
  not a design choice.
- **Verdict:** forced platform constraint.

### 20. Storage: PostgreSQL → JSON file store (conscious compromise)

- **Design (A1):** §2.4 ER diagram and §2.5 ADR show PostgreSQL with tables
  for DOCUMENTS, DOCUMENT_VERSIONS, DOCUMENT_PERMISSIONS, USERS, etc.
- **Implementation:** `backend/storage/json_store.py` — a thin read/write
  layer over flat JSON files in `backend/data/`. One file per collection
  (documents.json, versions.json, users.json, permissions.json,
  refresh_tokens.json).
- **Why:** Assignment 2 explicitly states "a database is not required —
  in-memory storage or file-based persistence (e.g., JSON files) is
  acceptable." JSON files persist across server restarts (unlike in-memory
  dicts), require zero infrastructure, and are sufficient for demo-scale load.
  The storage layer is abstracted behind a `get / put / delete / all_values`
  interface, so swapping in SQLAlchemy + PostgreSQL later is isolated to
  `json_store.py`.
- **Verdict:** conscious compromise. Correct at demo scale; upgrade path
  is a single-file change.

### 21. Authentication: external OIDC provider → self-issued JWT (forced by Assignment 2)

- **Design (A1):** §2.2 shows an external "Identity Provider (OAuth 2.0 /
  OIDC e.g. Google)" as a system boundary.
- **Implementation:** `backend/core/auth.py` issues HS256 JWTs directly
  (python-jose). Registration and login are handled by
  `backend/routers/auth.py`. No external provider is involved.
- **Why:** Assignment 2 mandates "JWT-based authentication" — self-issued
  tokens with short-lived access tokens (20 min) and refresh tokens (3 days).
  Adding an external OIDC dependency would complicate local setup without
  satisfying any grading criterion.
- **Verdict:** forced by Assignment 2 constraints. The JWT payload shape
  (`sub`, `email`, `username`) was chosen to match the OIDC `id_token`
  convention so a future migration to an external provider is additive.

### 22. Document content: `yjs_state bytea` → TipTap JSON (improvement)

- **Design (A1):** §2.4 ER diagram stores `yjs_state bytea` in the DOCUMENTS
  table (Yjs CRDT binary).
- **Implementation:** `content` is stored as TipTap's native JSON document
  (`{"type": "doc", "content": [...]}`) in the JSON store. Member 3's LWW
  collaboration layer also uses this format for broadcast payloads.
- **Why:** consistent with deviation 1 (LWW instead of Yjs CRDT). Storing
  TipTap JSON keeps the content human-readable, debuggable, and directly
  loadable by the editor without a CRDT library. Member 1's original
  implementation stored content as a plain string, which loses all formatting;
  TipTap JSON preserves headings, bold, lists, and code blocks faithfully.
- **Verdict:** improvement given the LWW collaboration model.

### 23. Document versions: inline array → separate collection (improvement)

- **Design (A1):** §2.4 ER diagram has a separate DOCUMENT_VERSIONS table
  linked by `document_id`.
- **Implementation:** `backend/data/versions.json` is a separate flat
  collection keyed by `version_id`. Member 1's original draft embedded
  versions as an inline array inside the document record, which made restore
  operations overwrite the entire document object.
- **Why:** keeping versions in a separate collection matches the A1 ER diagram
  exactly, avoids unbounded document object growth, and makes the restore
  endpoint a clean two-step (snapshot current → overwrite from version) with
  no risk of corrupting sibling fields.
- **Verdict:** improvement, aligned with A1 design.

### 24. Auth integration: Member 1's in-memory dicts → JSON-persisted users (improvement)

- **Design (A1):** no storage technology specified for users at the auth level.
- **Implementation:** Member 1's original `backend/app/storage.py` kept
  `users` and `refresh_tokens` as plain Python dicts (lost on server restart).
  Member 2 adapted the auth routes to use `json_store.py`, persisting users
  and refresh tokens to `backend/data/users.json` and
  `backend/data/refresh_tokens.json`.
- **Why:** restarting the backend during development or the demo would
  invalidate all registered accounts and force re-registration. Persistence
  is essential for a realistic demo flow.
- **Verdict:** improvement.

## Member 1 scope: auth, users, sharing, security

## 25. Authentication model: OAuth/OIDC -> self-issued JWT

**Design (A1):**  
The architecture specified authentication via an external Identity Provider using OAuth 2.0 / OIDC, with token validation handled by the backend.

**Implementation:**  
Authentication is implemented directly in the FastAPI backend using self-issued JWT tokens (HS256). The system supports:
- short-lived access tokens (20 minutes)
- long-lived refresh tokens (3 days)

Tokens are issued and validated entirely within the backend without external providers.

**Why:**  
Assignment 2 explicitly requires JWT-based authentication and does not require external identity providers. Integrating OAuth/OIDC would add significant complexity without improving the assignment outcome.

**Verdict:**  
Compromise forced by assignment scope, but aligned with Assignment 2 requirements.

---

## 26. Password storage: plaintext/unspecified -> bcrypt hashing

**Design (A1):**  
Authentication was required, but password hashing details were not fully specified.

**Implementation:**  
Passwords are hashed using bcrypt via Passlib. Plaintext passwords are never stored.

**Why:**  
Secure password storage is mandatory for any authentication system. Bcrypt provides salting and strong resistance to brute-force attacks.

**Impact:**  
- prevents exposure of raw credentials  
- improves backend security  
- adds small computation cost during login

**Verdict:**  
Improvement.

---

## 27. Refresh token handling: persistent session store -> in-memory token store

**Design (A1):**  
Session handling implied a more persistent session lifecycle.

**Implementation:**  
Refresh tokens are stored in-memory using a Python dictionary.

**Why:**  
Persistent token storage would require additional database integration. In-memory storage was sufficient for assignment scope and much faster to implement.

**Impact:**  
- refresh tokens are lost on server restart  
- no token revocation or rotation  
- simpler implementation

**Verdict:**  
Compromise.

---

## 28. Permission model: database-backed RBAC -> inline role mapping

**Design (A1):**  
Role-based access control was designed with persistent storage and more formal permission structures.

**Implementation:**  
Permissions are stored directly inside each document object using:
- `owner`
- `shared_with`

Permission checks are enforced using helper functions:
- `require_read`
- `require_edit`
- `require_owner`

**Why:**  
A full RBAC system with dedicated tables would significantly increase complexity. Inline role mapping made sharing and permission checks much easier to implement for the assignment.

**Impact:**  
- simpler logic  
- easier debugging  
- limited scalability and flexibility

**Verdict:**  
Compromise.

---

## 29. Server-side permission enforcement

**Design (A1):**  
Permissions had to be enforced at the API layer, not only in the UI.

**Implementation:**  
All protected document routes enforce permission checks in the backend before performing any operation. Direct API calls are validated the same way as frontend requests.

**Why:**  
This prevents unauthorized users from bypassing access control through manual API requests.

**Impact:**  
- stronger security  
- satisfies assignment requirement  
- ensures viewer/editor/owner roles are actually enforced

**Verdict:**  
Improvement and direct compliance with the original design.

---

## 30. Authentication middleware: distributed middleware design -> FastAPI dependency injection

**Design (A1):**  
The original design described centralized authentication middleware across services.

**Implementation:**  
FastAPI dependency injection is used through `Depends(get_current_user)` to enforce authentication on protected endpoints.

**Why:**  
FastAPI dependencies provide a clean and reusable way to enforce authentication without repeating code in every route.

**Impact:**  
- consistent auth enforcement  
- cleaner backend code  
- simpler integration with route handlers

**Verdict:**  
Improvement.
