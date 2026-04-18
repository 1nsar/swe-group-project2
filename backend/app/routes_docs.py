import uuid
from fastapi import APIRouter, Depends, HTTPException

from .models import DocumentCreate, DocumentUpdate, ShareRequest
from .storage import documents, now_iso
from .auth import get_current_user
from .permissions import require_read, require_edit, require_owner

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
def list_documents(current_user=Depends(get_current_user)):
    username = current_user["username"]
    result = []

    for doc in documents.values():
        if doc["owner"] == username or username in doc["shared_with"]:
            result.append(doc)

    return result


@router.post("")
def create_document(data: DocumentCreate, current_user=Depends(get_current_user)):
    doc_id = str(uuid.uuid4())

    doc = {
        "id": doc_id,
        "title": data.title,
        "content": data.content,
        "owner": current_user["username"],
        "shared_with": {},
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "versions": []
    }

    documents[doc_id] = doc
    return doc


@router.get("/{doc_id}")
def get_document(doc_id: str, current_user=Depends(get_current_user)):
    doc = documents.get(doc_id)

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    require_read(doc, current_user["username"])
    return doc


@router.put("/{doc_id}")
def update_document(doc_id: str, data: DocumentUpdate, current_user=Depends(get_current_user)):
    doc = documents.get(doc_id)

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    require_edit(doc, current_user["username"])

    doc["versions"].append({
        "content": doc["content"],
        "saved_at": now_iso()
    })

    doc["content"] = data.content
    doc["updated_at"] = now_iso()

    return doc


@router.post("/{doc_id}/share")
def share_document(doc_id: str, data: ShareRequest, current_user=Depends(get_current_user)):
    doc = documents.get(doc_id)

    if not doc: 
        raise HTTPException(status_code=404, detail="Document not found")

    require_owner(doc, current_user["username"])

    if data.role not in ["editor", "viewer"]:
        raise HTTPException(status_code=400, detail="Role must be editor or viewer")

    doc["shared_with"][data.username] = data.role

    return {"message": "Document shared successfully"}


@router.post("/{doc_id}/restore/{version_index}")
def restore_document_version(doc_id: str, version_index: int, current_user=Depends(get_current_user)):
    doc = documents.get(doc_id)

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    require_edit(doc, current_user["username"])

    if version_index < 0 or version_index >= len(doc["versions"]):
        raise HTTPException(status_code=400, detail="Invalid version index")

    old_version = doc["versions"][version_index]

    doc["versions"].append({
        "content": doc["content"],
        "saved_at": now_iso()
    })

    doc["content"] = old_version["content"]
    doc["updated_at"] = now_iso()

    return doc