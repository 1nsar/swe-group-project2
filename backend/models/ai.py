"""
AI interaction models and request/response schemas.

Assignment 1 alignment
----------------------
* §2.2 Capability Area B: action enum includes rewrite, summarize, translate,
  grammar, and a generic ``custom`` escape hatch. Restructure is deferred.
* §2.2 Privacy consideration: "AI interaction logs retain only a prompt hash
  and token count (no raw content) for 30 days." ``AIInteraction`` therefore
  stores a SHA-256 ``prompt_hash`` and token counts — not the raw selection,
  the rendered prompt, or the model output. An optional debug-only
  ``raw_output`` is gated behind an env flag so developers can inspect a
  specific interaction without persisting it by default.
* §2.2 Privacy (30-day retention) is enforced by a ``retention_expires_at``
  timestamp the history endpoint uses to filter out expired rows.

A single AIInteraction is the record of one request-response cycle including
its accept/reject decision.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Action = Literal["rewrite", "summarize", "translate", "grammar", "custom"]
Status = Literal[
    "pending",
    "streaming",
    "completed",
    "cancelled",
    "error",
    "accepted",
    "rejected",
]

# §2.2 Privacy: 30-day retention for AI interaction logs.
RETENTION_DAYS = 30


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retention_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def hash_prompt(messages: list[dict]) -> str:
    """SHA-256 over the canonical JSON of the rendered prompt messages."""
    payload = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AIInteraction(BaseModel):
    """
    Persisted record of a single AI call.

    Privacy: raw selection, rendered prompt, and raw output are **not** stored
    by default (§2.2 Privacy). We keep ``prompt_hash`` and token counts only.
    Action-level metadata (tone/length/target_language) is retained as it
    carries no user content.
    """

    id: str = Field(default_factory=_new_id)
    document_id: str
    user_id: str
    action: Action

    # Non-sensitive action metadata.
    tone: Optional[str] = None
    length: Optional[str] = None
    target_language: Optional[str] = None
    # NOTE: instruction text is user content — we hash it, we don't store it.
    instruction_hash: Optional[str] = None

    # Privacy-preserving audit fields.
    prompt_hash: str = ""
    selection_length: int = 0
    output_length: int = 0

    model: str = ""
    provider: str = ""

    status: Status = "pending"
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0

    created_at: str = Field(default_factory=_utcnow)
    completed_at: Optional[str] = None
    decided_at: Optional[str] = None  # when user accepted/rejected
    retention_expires_at: str = Field(default_factory=_retention_expiry)


class AIStreamRequest(BaseModel):
    """Body accepted by ``POST /api/ai/stream``.

    Mirrors the §2.2 AI Integration Design: selection + bounded before/after
    context + per-action options. ``target_language`` is required for
    ``translate`` (the router validates this).
    """

    document_id: str
    action: Action
    selection: str = ""
    context_before: str = ""
    context_after: str = ""
    tone: Optional[str] = "neutral"
    length: Optional[str] = "medium"
    instruction: Optional[str] = None
    target_language: Optional[str] = None


class AIDecision(BaseModel):
    """Body accepted by ``POST /api/ai/interactions/{id}/accept|reject``."""

    # Length is all we persist — raw applied text is never stored.
    applied_length: Optional[int] = None


class AIInteractionSummary(BaseModel):
    """Shape returned by the history endpoint (§2.2 Privacy — no raw content)."""

    id: str
    document_id: str
    user_id: str
    action: Action
    tone: Optional[str] = None
    length: Optional[str] = None
    target_language: Optional[str] = None
    prompt_hash: str
    selection_length: int
    output_length: int
    status: Status
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    created_at: str
    completed_at: Optional[str]
    decided_at: Optional[str]
    retention_expires_at: str

    @classmethod
    def from_interaction(cls, i: AIInteraction) -> "AIInteractionSummary":
        return cls(
            id=i.id,
            document_id=i.document_id,
            user_id=i.user_id,
            action=i.action,
            tone=i.tone,
            length=i.length,
            target_language=i.target_language,
            prompt_hash=i.prompt_hash,
            selection_length=i.selection_length,
            output_length=i.output_length,
            status=i.status,
            model=i.model,
            provider=i.provider,
            input_tokens=i.input_tokens,
            output_tokens=i.output_tokens,
            created_at=i.created_at,
            completed_at=i.completed_at,
            decided_at=i.decided_at,
            retention_expires_at=i.retention_expires_at,
        )
