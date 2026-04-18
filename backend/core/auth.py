"""
Auth logic — written by Member 1, adapted into Member 2's backend structure.
Original: backend/app/auth.py
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "20"))
REFRESH_TOKEN_MINUTES = int(os.getenv("REFRESH_TOKEN_MINUTES", "4320"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _make_token(data: dict, expires_minutes: int) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user: dict) -> str:
    return _make_token(
        {"sub": user["id"], "email": user["email"], "username": user["username"], "type": "access"},
        ACCESS_TOKEN_MINUTES,
    )


def create_refresh_token(user: dict) -> str:
    return _make_token(
        {"sub": user["id"], "type": "refresh"},
        REFRESH_TOKEN_MINUTES,
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
