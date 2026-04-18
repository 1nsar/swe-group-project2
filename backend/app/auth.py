from datetime import datetime, timedelta
import os

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv

from .storage import users, refresh_tokens

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "20"))
REFRESH_TOKEN_MINUTES = int(os.getenv("REFRESH_TOKEN_MINUTES", "4320"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def create_token(data: dict, expires_minutes: int):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user: dict):
    return create_token(
        {
            "sub": user["id"],
            "email": user["email"],
            "username": user["username"],
            "type": "access"
        },
        ACCESS_TOKEN_MINUTES
    )


def create_refresh_token(user: dict):
    token = create_token(
        {
            "sub": user["id"],
            "type": "refresh"
        },
        REFRESH_TOKEN_MINUTES
    )
    refresh_tokens[token] = user["id"]
    return token

def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")

    user_id = payload.get("sub")

    user = None
    for u in users.values():
        if u["id"] == user_id:
            user = u
            break

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user