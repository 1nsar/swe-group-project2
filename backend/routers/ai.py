"""
AI Writing Assistant router.

Endpoints
---------
GET  /api/ai/actions                                — list actions available to *this* user for *this* doc
GET  /api/ai/quota                                  — quota snapshot (used/limit/warning/reset_at)
POST /api/ai/stream                                 — SSE text/event-stream
POST /api/ai/interactions/{id}/accept
POST /api/ai/interactions/{id}/reject
GET  /api/ai/history/{document_id}
GET  /api/ai/interactions/{id}

Assignment 1 alignment
----------------------
* §2.2 Authentication: every REST route validates the JWT via the shared
  ``get_current_user`` dependency. Role enforcement happens in ``ai_authz``
  before any LLM work starts.
* §2.2 AI Integration Design (quotas): ``quota.check`` runs before streaming
  and ``quota.record`` runs after; an 80% snapshot is returned by ``/quota``.
* §2.2 Privacy: we store a SHA-256 prompt hash and token counts only — not
  the selection, the rendered prompt, or the output.
* §2.5 ADR-002 explicitly rejects SSE for AI result delivery in favour of
  Redis+WebSocket ``ai:pending``/``ai:result``/``ai:error``. We diverge from
  this ADR and document the trade-off in DEVIATIONS.md; the SSE event shape
  below mirrors the WebSocket event names so a future migration is mechanical.

Streaming strategy
------------------
FastAPI's ``StreamingResponse`` with ``text/event-stream``. Each event is JSON
with a ``type`` tag (``meta``, ``pending``, ``token``, ``done``, ``cancel``,
``error``). Cancellation maps to ``AbortController`` on the client, detected
server-side via ``request.is_disconnected()`` flipping a ``cancel_event``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.dependencies.auth import get_current_user
from backend.models.user import CurrentUser
from backend.models.ai import (
    AIDecision,
    AIInteraction,
    AIInteractionSummary,
    AIStreamRequest,
    hash_prompt,
)
from backend.services import ai_authz, quota
from backend.services.llm_provider import StreamResult, get_provider
from backend.services.prompts import (
    PromptContext,
    available_actions,
    build_messages,
)
from backend.storage import json_store as store

router = APIRouter(prefix="/api/ai", tags=["ai"])

INTERACTIONS = "ai_interactions"
DOCS = "documents"
PERMISSIONS = "permissions"


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_interaction_or_404(interaction_id: str) -> AIInteraction:
    raw = store.get(INTERACTIONS, interaction_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return AIInteraction(**raw)


def _save_interaction(interaction: AIInteraction) -> None:
    store.put(INTERACTIONS, interaction.id, interaction.model_dump())


def _sse(event: dict) -> bytes:
    """Encode a JSON payload as a single SSE event with blank-line terminator."""
    return f"data: {json.dumps(event)}\n\n".encode("utf-8")


def _hash_instruction(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_doc_or_404(document_id: str) -> dict:
    raw = store.get(DOCS, document_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Document not found")
    return raw


# ── routes ───────────────────────────────────────────────────────────────────


@router.get("/actions")
def list_actions(
    document_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Available AI actions.

    If ``document_id`` is passed we filter the list to only actions this user
    may invoke against *that* document (§2.2 role table). Without the param
    we fall back to the full template list for backwards compatibility.
    """
    actions = available_actions()
    if document_id is None:
        return {"actions": actions}

    doc = _load_doc_or_404(document_id)
    perms = store.all_values(PERMISSIONS)
    filtered = [a for a in actions if ai_authz.decide(doc, user.id, a, perms).allowed]
    return {"actions": filtered}


@router.get("/quota")
def get_quota(user: CurrentUser = Depends(get_current_user)):
    """Quota snapshot for the calling user (§2.2 cost control)."""
    return quota.peek(user.id).to_payload()


@router.post("/stream")
async def stream(
    body: AIStreamRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """
    Stream an AI completion as Server-Sent Events.

    Event types
    -----------
    ``meta``    : first frame with interaction_id, model, provider, action,
                  and a quota snapshot (used/limit/warning).
    ``pending`` : emitted before the first token so the UI can render an
                  "AI pending" indicator (mirrors §2.2's ``ai:pending``).
    ``token``   : repeated, contains ``delta`` text to append.
    ``done``    : final frame on success, with token usage + updated quota.
    ``error``   : any failure, includes ``code`` for AI_QUOTA_EXCEEDED /
                  AI_ACCESS_DENIED / AI_SERVICE_UNAVAILABLE.
    ``cancel``  : emitted if cancellation was detected server-side.
    """
    # 1. Action sanity (§1.4 FR-AI-05: always return a clear error).
    if body.action not in available_actions():
        raise HTTPException(status_code=400, detail=f"Unknown AI action: {body.action!r}")

    # Translate needs a target language.
    if body.action == "translate" and not (body.target_language or "").strip():
        raise HTTPException(
            status_code=400,
            detail="target_language is required for the 'translate' action",
        )

    # 2. Document access + role gating (§2.2 role table).
    doc = _load_doc_or_404(body.document_id)
    perms = store.all_values(PERMISSIONS)
    decision = ai_authz.decide(doc, user.id, body.action, perms)
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={"code": "AI_ACCESS_DENIED", "reason": decision.reason, "role": decision.role},
        )

    # 3. Quota pre-check (§2.2 cost control).
    try:
        quota_snap = quota.check(user.id)
    except quota.QuotaExceeded as qe:
        raise HTTPException(status_code=429, detail=qe.to_payload())

    # 4. Build the prompt.
    try:
        ctx = PromptContext(
            selection=body.selection or "",
            context_before=body.context_before or "",
            context_after=body.context_after or "",
            tone=body.tone or "neutral",
            length=body.length or "medium",
            instruction=body.instruction or "",
            target_language=body.target_language or "English",
        )
        messages = build_messages(body.action, ctx)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    provider = get_provider()
    rendered = [{"role": m.role, "content": m.content} for m in messages]

    # 5. Persist a privacy-compliant interaction row.
    interaction = AIInteraction(
        document_id=body.document_id,
        user_id=user.id,
        action=body.action,
        tone=body.tone,
        length=body.length,
        target_language=body.target_language,
        instruction_hash=_hash_instruction(body.instruction),
        prompt_hash=hash_prompt(rendered),
        selection_length=len(body.selection or ""),
        model=provider.default_model,
        provider=provider.name,
        status="streaming",
    )
    _save_interaction(interaction)

    cancel_event = asyncio.Event()
    result = StreamResult()

    async def event_generator() -> AsyncIterator[bytes]:
        yield _sse(
            {
                "type": "meta",
                "interaction_id": interaction.id,
                "action": body.action,
                "model": provider.default_model,
                "provider": provider.name,
                "role": decision.role,
                "quota": quota_snap.to_payload(),
            }
        )
        # Mirrors §2.2's ``ai:pending`` WebSocket event. Other collaborators
        # cannot observe this in the baseline (see DEVIATIONS) but the event
        # exists so the local UI can flip to a "pending" state.
        yield _sse({"type": "pending", "interaction_id": interaction.id})

        async def _watch_disconnect() -> None:
            while not cancel_event.is_set():
                try:
                    if await request.is_disconnected():
                        cancel_event.set()
                        return
                except Exception:
                    return
                await asyncio.sleep(0.25)

        watcher = asyncio.create_task(_watch_disconnect())

        try:
            async for chunk in provider.stream(
                messages,
                cancel_event=cancel_event,
                result=result,
            ):
                yield _sse({"type": "token", "delta": chunk})

            if result.finished:
                interaction.status = "completed"
                interaction.output_length = len(result.text or "")
                interaction.model = result.model or provider.default_model
                interaction.input_tokens = result.input_tokens
                interaction.output_tokens = result.output_tokens
                interaction.completed_at = datetime.now(timezone.utc).isoformat()
                _save_interaction(interaction)

                # Commit tokens to the user's monthly quota.
                post_snap = quota.record(
                    user.id, result.input_tokens + result.output_tokens
                )
                yield _sse(
                    {
                        "type": "done",
                        "interaction_id": interaction.id,
                        # §2.2 Privacy: we still emit the rendered ``output``
                        # over the wire — that's required for the UI to show
                        # the diff and let the user Accept — but we don't
                        # persist it.
                        "output": result.text,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "quota": post_snap.to_payload(),
                    }
                )
            else:
                interaction.status = "cancelled"
                _save_interaction(interaction)
                yield _sse({"type": "cancel", "interaction_id": interaction.id})
        except Exception as e:
            interaction.status = "error"
            interaction.error = str(e)
            _save_interaction(interaction)
            yield _sse(
                {
                    "type": "error",
                    "code": "AI_SERVICE_UNAVAILABLE",
                    "interaction_id": interaction.id,
                    "message": str(e),
                }
            )
        finally:
            watcher.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if reverse-proxied
        },
    )


@router.get("/interactions/{interaction_id}", response_model=AIInteractionSummary)
def get_interaction(
    interaction_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> AIInteractionSummary:
    interaction = _load_interaction_or_404(interaction_id)
    if interaction.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your interaction")
    return AIInteractionSummary.from_interaction(interaction)


@router.post("/interactions/{interaction_id}/accept", response_model=AIInteractionSummary)
def accept_interaction(
    interaction_id: str,
    decision: AIDecision,
    user: CurrentUser = Depends(get_current_user),
) -> AIInteractionSummary:
    interaction = _load_interaction_or_404(interaction_id)
    if interaction.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your interaction")
    if decision.applied_length is not None:
        interaction.output_length = int(decision.applied_length)
    interaction.status = "accepted"
    interaction.decided_at = datetime.now(timezone.utc).isoformat()
    _save_interaction(interaction)
    return AIInteractionSummary.from_interaction(interaction)


@router.post("/interactions/{interaction_id}/reject", response_model=AIInteractionSummary)
def reject_interaction(
    interaction_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> AIInteractionSummary:
    interaction = _load_interaction_or_404(interaction_id)
    if interaction.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your interaction")
    interaction.status = "rejected"
    interaction.decided_at = datetime.now(timezone.utc).isoformat()
    _save_interaction(interaction)
    return AIInteractionSummary.from_interaction(interaction)


@router.get("/history/{document_id}", response_model=list[AIInteractionSummary])
def history(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> list[AIInteractionSummary]:
    """AI interaction history for a document, most-recent first.

    §2.2 Privacy: rows past their 30-day ``retention_expires_at`` are filtered
    out on read (a background sweep that deletes them is a future task).
    The caller's own interactions only — we document this in DEVIATIONS.md.
    """
    now = datetime.now(timezone.utc).isoformat()
    records: list[AIInteraction] = []
    for r in store.all_values(INTERACTIONS):
        if r["document_id"] != document_id:
            continue
        if r.get("user_id") != user.id:
            continue
        if r.get("retention_expires_at") and r["retention_expires_at"] <= now:
            continue
        records.append(AIInteraction(**r))
    records.sort(key=lambda r: r.created_at, reverse=True)
    return [AIInteractionSummary.from_interaction(r) for r in records]
