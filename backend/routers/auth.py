"""
Auth routes — written by Member 1, adapted into Member 2's backend structure.
Original: backend/app/routes_auth.py
Changes: relative imports → absolute; storage uses JSON files instead of in-memory dicts.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from backend.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from backend.storage import json_store as store

router = APIRouter(prefix="/auth", tags=["auth"])

USERS = "users"
REFRESH_TOKENS = "refresh_tokens"


class UserCreate(BaseModel):
    username: str
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/register", status_code=201)
def register(data: UserCreate):
    all_users = store.all_values(USERS)

    if any(u["username"] == data.username for u in all_users):
        raise HTTPException(status_code=400, detail="Username already exists")
    if any(u["email"] == data.email for u in all_users):
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "username": data.username,
        "email": data.email,
        "password": hash_password(data.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.put(USERS, user_id, user)
    return {"message": "Registered successfully"}


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    all_users = store.all_values(USERS)
    user = next((u for u in all_users if u["username"] == form_data.username), None)

    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)

    store.put(REFRESH_TOKENS, refresh_token, {"user_id": user["id"]})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh")
def refresh(data: RefreshRequest):
    record = store.get(REFRESH_TOKENS, data.refresh_token)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    payload = decode_token(data.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token type")

    all_users = store.all_values(USERS)
    user = next((u for u in all_users if u["id"] == payload["sub"]), None)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return {"access_token": create_access_token(user)}
