"""
Role-based authorization for the AI Writing Assistant.

Assignment 1 alignment
----------------------
§2.2 Authentication and Authorisation table:

  Owner      - all AI actions
  Editor     - all AI actions
  Commenter  - summarise + translate only
  Viewer     - no AI

§2.2 AI Integration Design also says: "Owners can disable AI features per
document." We honour the document-level ``ai_enabled`` flag if it is present
on the document record (Member 2's ``Document`` model doesn't ship one yet, so
we treat missing-as-True and keep a TODO).

This module is intentionally small and has no dependency on FastAPI so it can
be used from both the REST router and, later, from a WebSocket AI endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

# Action strings must match backend.models.ai.Action.
_ALL_ACTIONS = {"rewrite", "summarize", "translate", "grammar", "custom"}
_COMMENTER_ACTIONS = {"summarize", "translate"}

# Canonicalised role strings. Anything not in this set is treated as "viewer".
KNOWN_ROLES = {"owner", "editor", "commenter", "viewer"}


@dataclass
class AccessDecision:
    allowed: bool
    role: str
    reason: Optional[str] = None  # human-readable if denied


def _resolve_role(
    document: dict,
    user_id: str,
    permissions: Iterable[dict],
) -> str:
    """Return the role of ``user_id`` on ``document``.

    - Document owner always resolves to "owner".
    - Otherwise we look up a matching row in the permissions collection.
    - If none matches the user is an outsider; return "viewer" so they get the
      most restrictive default and the router returns 403.
    """
    if document.get("owner_id") == user_id:
        return "owner"
    for p in permissions:
        if p.get("document_id") == document.get("id") and p.get("user_id") == user_id:
            role = str(p.get("role", "")).lower().strip()
            return role if role in KNOWN_ROLES else "viewer"
    return "viewer"


def decide(
    document: dict,
    user_id: str,
    action: str,
    permissions: Iterable[dict],
) -> AccessDecision:
    """Decide whether ``user_id`` may perform ``action`` on ``document``.

    Caller must pass the permissions collection (e.g. ``store.all_values("permissions")``)
    to keep this module storage-agnostic.
    """
    # §2.2 AI Integration Design: owners can disable AI per document.
    if document.get("ai_enabled") is False:
        return AccessDecision(
            allowed=False,
            role=_resolve_role(document, user_id, permissions),
            reason="AI features are disabled for this document",
        )

    if action not in _ALL_ACTIONS:
        return AccessDecision(allowed=False, role="unknown", reason=f"Unknown action {action!r}")

    role = _resolve_role(document, user_id, permissions)

    if role in ("owner", "editor"):
        return AccessDecision(allowed=True, role=role)
    if role == "commenter":
        if action in _COMMENTER_ACTIONS:
            return AccessDecision(allowed=True, role=role)
        return AccessDecision(
            allowed=False,
            role=role,
            reason=f"Commenters may only invoke: {', '.join(sorted(_COMMENTER_ACTIONS))}",
        )
    # viewer / unknown
    return AccessDecision(
        allowed=False,
        role=role,
        reason="Viewers may not invoke AI features",
    )
