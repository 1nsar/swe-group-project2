from __future__ import annotations
from pydantic import BaseModel
from typing import Any, Optional
import uuid
from datetime import datetime, timezone


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class Document(BaseModel):
    id: str
    title: str
    content: Any  # TipTap JSON
    owner_id: str
    created_at: str
    updated_at: str

    @classmethod
    def create(cls, title: str, owner_id: str) -> "Document":
        now = utcnow()
        return cls(
            id=new_id(),
            title=title,
            content={"type": "doc", "content": []},
            owner_id=owner_id,
            created_at=now,
            updated_at=now,
        )


class DocumentVersion(BaseModel):
    id: str
    document_id: str
    version_number: int
    content: Any
    title: str
    saved_by: str
    saved_at: str

    @classmethod
    def create(cls, doc: Document, version_number: int, saved_by: str) -> "DocumentVersion":
        return cls(
            id=new_id(),
            document_id=doc.id,
            version_number=version_number,
            content=doc.content,
            title=doc.title,
            saved_by=saved_by,
            saved_at=utcnow(),
        )


# --- Request / Response schemas ---

class DocumentCreate(BaseModel):
    title: str = "Untitled Document"


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[Any] = None


class DocumentSummary(BaseModel):
    id: str
    title: str
    owner_id: str
    created_at: str
    updated_at: str


class VersionSummary(BaseModel):
    id: str
    version_number: int
    title: str
    saved_by: str
    saved_at: str
