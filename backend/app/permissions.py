from fastapi import HTTPException


def get_user_role(doc: dict, username: str):
    if doc["owner"] == username:
        return "owner"
    return doc["shared_with"].get(username)


def require_read(doc: dict, username: str):
    role = get_user_role(doc, username)
    if role not in ["owner", "editor", "viewer"]:
        raise HTTPException(status_code=403, detail="No read permission")
    return role


def require_edit(doc: dict, username: str):
    role = get_user_role(doc, username)
    if role not in ["owner", "editor"]:
        raise HTTPException(status_code=403, detail="No edit permission")
    return role


def require_owner(doc: dict, username: str):
    role = get_user_role(doc, username)
    if role != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return role