"""
API integration tests for the AI router (Assignment 2 §3, §4.1).

Covers
------
* /api/ai/actions — lists available prompt templates, filterable by document
* /api/ai/quota   — returns the calling user's quota snapshot
* /api/ai/stream  — streams token-by-token SSE events; creates a privacy-
                    compliant interaction record; enforces role gating and
                    monthly quota
* /api/ai/interactions/{id}/accept|reject — flips status correctly, rejects
                    cross-user access
* /api/ai/history — returns only the caller's interactions
* Authentication is required on every route

Path B (Assignment 1 alignment) additions
-----------------------------------------
* Role gating per §2.2 role table: viewer blocked, commenter limited to
  summarize/translate, editor/owner full.
* Translate per §1.2 FR-AI-03: target_language is required.
* Monthly token quotas per §2.2 Cost Control: AI_QUOTA_EXCEEDED at 100%.
* Privacy per §2.2 AI Integration Design: stored row carries only
  prompt_hash + selection_length + output_length + token counts — no raw
  selection/prompt/output text.
"""
from __future__ import annotations

import json

from backend.tests.conftest import make_jwt


# ── helpers ─────────────────────────────────────────────────────────────────


def _auth(sub: str = "user-1", username: str = "alice") -> dict:
    from backend.storage import json_store as store
    store.put("users", sub, {"id": sub, "username": username, "email": f"{username}@test.example", "password": "x"})
    return {"Authorization": f"Bearer {make_jwt(sub=sub, username=username)}"}


def _parse_sse(body: str) -> list[dict]:
    """Split an SSE body into JSON-decoded events."""
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        data_lines = [l[5:].lstrip() for l in block.splitlines() if l.startswith("data:")]
        if not data_lines:
            continue
        events.append(json.loads("\n".join(data_lines)))
    return events


def _seed_doc(doc_id: str, owner_id: str = "user-1", ai_enabled: bool = True) -> None:
    """Put a minimal Document row into the shared json store so the router
    can look it up and role-gate against it."""
    from backend.storage import json_store as store

    store.put(
        "documents",
        doc_id,
        {
            "id": doc_id,
            "owner_id": owner_id,
            "title": "Test doc",
            "ai_enabled": ai_enabled,
        },
    )


def _seed_permission(doc_id: str, user_id: str, role: str) -> None:
    """Put a Permission row so role lookups resolve for non-owners."""
    from backend.storage import json_store as store

    key = f"{doc_id}:{user_id}"
    store.put(
        "permissions",
        key,
        {"document_id": doc_id, "user_id": user_id, "role": role},
    )


# ── actions + quota ─────────────────────────────────────────────────────────


def test_actions_endpoint_lists_templates(client):
    r = client.get("/api/ai/actions", headers=_auth())
    assert r.status_code == 200
    actions = r.json()["actions"]
    # Path B ships translate alongside rewrite/summarize/grammar/custom.
    assert {"rewrite", "summarize", "translate", "grammar", "custom"}.issubset(set(actions))


def test_actions_endpoint_filters_by_document_for_commenter(client):
    """§2.2 role table — commenters may only summarize + translate."""
    _seed_doc("doc-perm-1", owner_id="owner-x")
    _seed_permission("doc-perm-1", "user-1", "commenter")

    r = client.get("/api/ai/actions?document_id=doc-perm-1", headers=_auth())
    assert r.status_code == 200
    actions = set(r.json()["actions"])
    assert actions == {"summarize", "translate"}


def test_actions_endpoint_empty_for_viewer(client):
    _seed_doc("doc-perm-2", owner_id="owner-x")
    _seed_permission("doc-perm-2", "user-1", "viewer")

    r = client.get("/api/ai/actions?document_id=doc-perm-2", headers=_auth())
    assert r.status_code == 200
    assert r.json()["actions"] == []


def test_quota_endpoint_returns_snapshot(client):
    r = client.get("/api/ai/quota", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert {"used", "limit", "remaining", "fraction", "warning", "reset_at"}.issubset(body.keys())
    assert body["used"] == 0
    assert body["warning"] is False


# ── streaming: happy path ───────────────────────────────────────────────────


def test_stream_happy_path_creates_and_completes_interaction(client):
    _seed_doc("doc-42")
    body = {
        "document_id": "doc-42",
        "action": "rewrite",
        "selection": "The weather is nice today.",
        "tone": "formal",
    }
    r = client.post("/api/ai/stream", json=body, headers=_auth())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert types[1] == "pending"  # §2.2 ai:pending mirrored on SSE
    assert "token" in types
    assert types[-1] == "done"

    interaction_id = events[0]["interaction_id"]

    # The meta event carries role + a quota snapshot.
    assert events[0]["role"] in {"owner", "editor", "commenter", "viewer"}
    assert "quota" in events[0]
    # The done event carries updated quota.
    assert "quota" in events[-1]

    # History should include this interaction, status=completed.
    hist = client.get("/api/ai/history/doc-42", headers=_auth()).json()
    assert any(h["id"] == interaction_id and h["status"] == "completed" for h in hist)


def test_stream_requires_auth(client):
    _seed_doc("doc-1")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-1", "action": "rewrite", "selection": "x"},
    )
    assert r.status_code in (401, 403)


def test_stream_rejects_unknown_action(client):
    _seed_doc("doc-1")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-1", "action": "not-real", "selection": "x"},
        headers=_auth(),
    )
    # Pydantic rejects unknown literal → 422.
    assert r.status_code in (400, 422)


def test_stream_404_when_document_missing(client):
    """No doc row → not an auth problem, a 404."""
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "does-not-exist", "action": "rewrite", "selection": "x"},
        headers=_auth(),
    )
    assert r.status_code == 404


# ── role gating (§2.2 role table) ───────────────────────────────────────────


def test_stream_denied_for_viewer_with_access_denied_code(client):
    _seed_doc("doc-view", owner_id="owner-x")
    _seed_permission("doc-view", "user-1", "viewer")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-view", "action": "rewrite", "selection": "x"},
        headers=_auth(),
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "AI_ACCESS_DENIED"
    assert detail["role"] == "viewer"


def test_stream_denied_for_outsider_treated_as_viewer(client):
    """User not listed in permissions on a doc they don't own → viewer → 403."""
    _seed_doc("doc-outsider", owner_id="owner-x")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-outsider", "action": "rewrite", "selection": "x"},
        headers=_auth(),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "AI_ACCESS_DENIED"


def test_stream_commenter_can_summarize(client):
    _seed_doc("doc-com", owner_id="owner-x")
    _seed_permission("doc-com", "user-1", "commenter")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-com", "action": "summarize", "selection": "Lorem"},
        headers=_auth(),
    )
    assert r.status_code == 200


def test_stream_commenter_blocked_from_rewrite(client):
    _seed_doc("doc-com2", owner_id="owner-x")
    _seed_permission("doc-com2", "user-1", "commenter")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-com2", "action": "rewrite", "selection": "Lorem"},
        headers=_auth(),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "AI_ACCESS_DENIED"
    assert r.json()["detail"]["role"] == "commenter"


def test_stream_denied_when_ai_disabled_on_document(client):
    """§2.2 AI Integration Design — owners can disable AI per document."""
    _seed_doc("doc-off", owner_id="user-1", ai_enabled=False)
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-off", "action": "rewrite", "selection": "x"},
        headers=_auth(),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "AI_ACCESS_DENIED"


# ── translate requires a target language ────────────────────────────────────


def test_translate_requires_target_language(client):
    _seed_doc("doc-tr")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-tr", "action": "translate", "selection": "hello"},
        headers=_auth(),
    )
    # Router explicitly returns 400 with a clear message.
    assert r.status_code == 400
    assert "target_language" in r.json()["detail"]


def test_translate_with_target_language_succeeds(client):
    _seed_doc("doc-tr2")
    r = client.post(
        "/api/ai/stream",
        json={
            "document_id": "doc-tr2",
            "action": "translate",
            "selection": "hello",
            "target_language": "French",
        },
        headers=_auth(),
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "done"


# ── monthly quota ───────────────────────────────────────────────────────────


def test_quota_exceeded_blocks_stream(client):
    """§2.2 Cost Control — 100% quota → AI_QUOTA_EXCEEDED."""
    from backend.services import quota

    _seed_doc("doc-quota")
    # Set a zero limit so any attempt trips quota.check.
    quota.set_limit("user-1", 0)

    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-quota", "action": "rewrite", "selection": "x"},
        headers=_auth(),
    )
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["code"] == "AI_QUOTA_EXCEEDED"
    assert "limit" in detail and "used" in detail and "reset_at" in detail


def test_quota_warning_flag_flips_at_80_percent(client):
    from backend.services import quota

    quota.set_limit("user-1", 100)
    quota.record("user-1", 79)
    assert quota.peek("user-1").warning is False
    quota.record("user-1", 1)  # → 80
    assert quota.peek("user-1").warning is True


# ── privacy: no raw text persisted ──────────────────────────────────────────


def test_interaction_record_is_privacy_compliant(client):
    """§2.2 AI Integration Design — persisted row must be hashes + lengths."""
    from backend.storage import json_store as store

    _seed_doc("doc-priv")
    body = {
        "document_id": "doc-priv",
        "action": "rewrite",
        "selection": "SECRET-SELECTION-TEXT",
        "tone": "neutral",
        "instruction": "SECRET-INSTRUCTION-TEXT",
    }
    r = client.post("/api/ai/stream", json=body, headers=_auth())
    assert r.status_code == 200
    events = _parse_sse(r.text)
    interaction_id = events[0]["interaction_id"]

    raw = store.get("ai_interactions", interaction_id)
    assert raw is not None

    # No raw text fields of any kind.
    flat = json.dumps(raw)
    assert "SECRET-SELECTION-TEXT" not in flat
    assert "SECRET-INSTRUCTION-TEXT" not in flat
    assert "input_text" not in raw
    assert "prompt_used" not in raw
    assert "output_text" not in raw

    # The privacy-compliant fields are there.
    assert raw["prompt_hash"] and len(raw["prompt_hash"]) == 64  # sha-256 hex
    assert raw["selection_length"] == len("SECRET-SELECTION-TEXT")
    assert raw["instruction_hash"] and len(raw["instruction_hash"]) == 64
    assert "retention_expires_at" in raw


def test_history_omits_rows_past_retention(client):
    """§2.2 Privacy — 30-day retention. We simulate expiry in-place."""
    from backend.storage import json_store as store
    from datetime import datetime, timezone, timedelta

    _seed_doc("doc-retention")
    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-retention", "action": "rewrite", "selection": "x"},
        headers=_auth(),
    )
    assert r.status_code == 200
    interaction_id = _parse_sse(r.text)[0]["interaction_id"]

    # Push retention into the past and persist.
    raw = store.get("ai_interactions", interaction_id)
    raw["retention_expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    store.put("ai_interactions", interaction_id, raw)

    hist = client.get("/api/ai/history/doc-retention", headers=_auth()).json()
    assert all(h["id"] != interaction_id for h in hist)


# ── accept / reject ─────────────────────────────────────────────────────────


def test_accept_and_reject_change_status(client):
    _seed_doc("doc-7")
    body = {
        "document_id": "doc-7",
        "action": "summarize",
        "selection": "Lorem ipsum dolor sit amet.",
        "length": "short",
    }
    r = client.post("/api/ai/stream", json=body, headers=_auth())
    events = _parse_sse(r.text)
    interaction_id = events[0]["interaction_id"]

    # Path B: accept now carries only applied_length (not raw applied_text).
    r2 = client.post(
        f"/api/ai/interactions/{interaction_id}/accept",
        json={"applied_length": 13},
        headers=_auth(),
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "accepted"
    assert r2.json()["output_length"] == 13

    # Flipping to reject should also succeed (idempotent semantics).
    r3 = client.post(
        f"/api/ai/interactions/{interaction_id}/reject",
        headers=_auth(),
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "rejected"


def test_cannot_access_another_users_interaction(client):
    _seed_doc("doc-8")
    body = {
        "document_id": "doc-8",
        "action": "rewrite",
        "selection": "text",
        "tone": "neutral",
    }
    r = client.post("/api/ai/stream", json=body, headers=_auth())
    interaction_id = _parse_sse(r.text)[0]["interaction_id"]

    # Try to accept as user-2.
    other = _auth(sub="user-2", username="bob")
    r2 = client.post(
        f"/api/ai/interactions/{interaction_id}/accept",
        json={},
        headers=other,
    )
    assert r2.status_code == 403


def test_history_returns_only_caller_interactions(client):
    _seed_doc("doc-99", owner_id="user-1")
    # Give user-2 editor access on doc-99 so they're allowed through the
    # gate, but their history view should still be scoped to themselves.
    _seed_permission("doc-99", "user-2", "editor")

    r = client.post(
        "/api/ai/stream",
        json={"document_id": "doc-99", "action": "grammar", "selection": "teh cat"},
        headers=_auth(),
    )
    assert r.status_code == 200

    # user-2 sees an empty list for that doc (per-caller scoping).
    other = _auth(sub="user-2", username="bob")
    hist = client.get("/api/ai/history/doc-99", headers=other).json()
    assert hist == []
