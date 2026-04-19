from __future__ import annotations
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.models.document import (
    Document, DocumentVersion,
    DocumentCreate, DocumentUpdate,
    DocumentSummary, VersionSummary,
    utcnow,
)
from backend.models.user import CurrentUser
from backend.dependencies.auth import get_current_user
from backend.storage import json_store as store

router = APIRouter(prefix="/api/documents", tags=["documents"])

DOCS = "documents"
VERSIONS = "versions"
PERMISSIONS = "permissions"
SHARE_LINKS = "share_links"

SHARE_LINK_TTL_DAYS = 7


class ShareLinkRequest(BaseModel):
    role: str = "editor"  # "viewer" or "editor"


class ShareLinkResponse(BaseModel):
    token: str
    role: str
    expires_at: str
    url: str


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_doc_or_404(doc_id: str) -> dict:
    doc = store.get(DOCS, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _check_access(doc: dict, user_id: str, require_write: bool = False) -> None:
    """
    Owner always has full access.
    For others, check permissions collection (written by Member 1).
    Falls back to owner-only if permissions file doesn't exist yet.
    """
    if doc["owner_id"] == user_id:
        return

    perms = store.all_values(PERMISSIONS)
    user_perm = next(
        (p for p in perms if p["document_id"] == doc["id"] and p["user_id"] == user_id),
        None,
    )
    if not user_perm:
        raise HTTPException(status_code=403, detail="Access denied")
    if require_write and user_perm["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewers cannot edit")


def _next_version_number(doc_id: str) -> int:
    versions = [v for v in store.all_values(VERSIONS) if v["document_id"] == doc_id]
    return max((v["version_number"] for v in versions), default=0) + 1


def _save_version(doc: Document, saved_by: str) -> DocumentVersion:
    version = DocumentVersion.create(
        doc=doc,
        version_number=_next_version_number(doc.id),
        saved_by=saved_by,
    )
    store.put(VERSIONS, version.id, version.model_dump())
    return version


# ── routes ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[DocumentSummary])
def list_documents(user: CurrentUser = Depends(get_current_user)):
    """Return all documents the user owns or has been granted access to."""
    all_docs = store.all_values(DOCS)
    owned = [d for d in all_docs if d["owner_id"] == user.id]

    shared_ids = {
        p["document_id"]
        for p in store.all_values(PERMISSIONS)
        if p["user_id"] == user.id
    }
    shared = [d for d in all_docs if d["id"] in shared_ids and d["owner_id"] != user.id]

    result = owned + shared
    result.sort(key=lambda d: d["updated_at"], reverse=True)
    return result


@router.post("", response_model=Document, status_code=status.HTTP_201_CREATED)
def create_document(
    body: DocumentCreate,
    user: CurrentUser = Depends(get_current_user),
):
    doc = Document.create(title=body.title, owner_id=user.id)
    store.put(DOCS, doc.id, doc.model_dump())
    _save_version(doc, saved_by=user.id)
    return doc


@router.get("/{doc_id}", response_model=Document)
def get_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = _get_doc_or_404(doc_id)
    _check_access(doc, user.id)
    return doc


@router.put("/{doc_id}", response_model=Document)
def update_document(
    doc_id: str,
    body: DocumentUpdate,
    user: CurrentUser = Depends(get_current_user),
):
    """Auto-save endpoint — updates content and/or title."""
    raw = _get_doc_or_404(doc_id)
    _check_access(raw, user.id, require_write=True)

    if body.title is not None:
        raw["title"] = body.title
    if body.content is not None:
        raw["content"] = body.content
    raw["updated_at"] = utcnow()

    store.put(DOCS, doc_id, raw)
    return raw


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = _get_doc_or_404(doc_id)
    if doc["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete")
    store.delete(DOCS, doc_id)
    # remove associated versions
    for v in store.all_values(VERSIONS):
        if v["document_id"] == doc_id:
            store.delete(VERSIONS, v["id"])


# ── version history ───────────────────────────────────────────────────────────

@router.get("/{doc_id}/versions", response_model=list[VersionSummary])
def list_versions(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = _get_doc_or_404(doc_id)
    _check_access(doc, user.id)
    versions = [v for v in store.all_values(VERSIONS) if v["document_id"] == doc_id]
    versions.sort(key=lambda v: v["version_number"], reverse=True)
    return versions


@router.post("/{doc_id}/versions", response_model=VersionSummary, status_code=status.HTTP_201_CREATED)
def save_version(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    """Manually snapshot the current document state as a named version."""
    raw = _get_doc_or_404(doc_id)
    _check_access(raw, user.id, require_write=True)
    doc = Document(**raw)
    version = _save_version(doc, saved_by=user.id)
    return version


@router.post("/{doc_id}/versions/{version_id}/restore", response_model=Document)
def restore_version(
    doc_id: str,
    version_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Restore document content to a previous version (saves current as snapshot first)."""
    raw = _get_doc_or_404(doc_id)
    _check_access(raw, user.id, require_write=True)

    version = store.get(VERSIONS, version_id)
    if not version or version["document_id"] != doc_id:
        raise HTTPException(status_code=404, detail="Version not found")

    # snapshot current state before overwriting
    _save_version(Document(**raw), saved_by=user.id)

    raw["content"] = version["content"]
    raw["title"] = version["title"]
    raw["updated_at"] = utcnow()
    store.put(DOCS, doc_id, raw)
    return raw


# ── share-by-link ─────────────────────────────────────────────────────────────

@router.post("/{doc_id}/share-link", response_model=ShareLinkResponse)
def create_share_link(
    doc_id: str,
    body: ShareLinkRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Generate a one-time-use shareable link for the document (owner only)."""
    doc = _get_doc_or_404(doc_id)
    if doc["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can share")
    if body.role not in ("viewer", "editor"):
        raise HTTPException(status_code=400, detail="role must be 'viewer' or 'editor'")

    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SHARE_LINK_TTL_DAYS)).isoformat()

    store.put(SHARE_LINKS, token, {
        "id": token,
        "document_id": doc_id,
        "role": body.role,
        "created_by": user.id,
        "expires_at": expires_at,
        "used": False,
    })

    return ShareLinkResponse(
        token=token,
        role=body.role,
        expires_at=expires_at,
        url=f"/join/{token}",
    )


@router.post("/join/{token}", response_model=dict)
def join_via_share_link(
    token: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Accept a share link — grants the calling user access and returns the doc id."""
    link = store.get(SHARE_LINKS, token)
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found or expired")

    now = datetime.now(timezone.utc).isoformat()
    if link["expires_at"] < now:
        raise HTTPException(status_code=410, detail="Share link has expired")

    doc_id = link["document_id"]
    doc = _get_doc_or_404(doc_id)

    # Owner doesn't need a permission row.
    if doc["owner_id"] != user.id:
        # Check if user already has access — don't add duplicate rows.
        perms = store.all_values(PERMISSIONS)
        existing = next(
            (p for p in perms if p["document_id"] == doc_id and p["user_id"] == user.id),
            None,
        )
        if not existing:
            perm_id = str(uuid.uuid4())
            store.put(PERMISSIONS, perm_id, {
                "id": perm_id,
                "document_id": doc_id,
                "user_id": user.id,
                "role": link["role"],
                "granted_at": now,
            })

    return {"document_id": doc_id, "role": link["role"], "title": doc.get("title", "")}
