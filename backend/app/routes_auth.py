import uuid
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import APIRouter, HTTPException,Depends
from .models import UserCreate, UserLogin, RefreshRequest, TokenResponse
from .storage import users, refresh_tokens, now_iso
from .auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register(data: UserCreate):
    if data.username in users:
        raise HTTPException(status_code=400, detail="Username already exists")

    users[data.username] = {
        "id": str(uuid.uuid4()),   
        "username": data.username,
        "email": data.email,
        "password": hash_password(data.password),
        "created_at": now_iso(),
    }

    return {"message": "Registered successfully"}


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users.get(form_data.username)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(form_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh")
def refresh(data: RefreshRequest):
    token = data.refresh_token

    if token not in refresh_tokens:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    payload = decode_token(token)

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token type")

    user_id = payload.get("sub")

    # find user by id
    user = None
    for u in users.values():
        if u["id"] == user_id:
            user = u
            break

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return {
        "access_token": create_access_token(user)
    }