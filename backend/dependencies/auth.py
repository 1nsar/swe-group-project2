"""
Real JWT auth dependency — integrates Member 1's auth logic.
Replaces the dev stub. get_current_user is imported by all routers.
"""
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from backend.core.auth import decode_token
from backend.models.user import CurrentUser
from backend.storage import json_store as store

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    payload = decode_token(token)

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")

    user_id = payload.get("sub")
    all_users = store.all_values("users")
    user = next((u for u in all_users if u["id"] == user_id), None)

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return CurrentUser(
        id=user["id"],
        email=user["email"],
        username=user["username"],
    )
