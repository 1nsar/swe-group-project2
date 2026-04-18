"""
WebSocket auth dependency.

Mirrors the approach in backend/dependencies/auth.py: try to decode a JWT,
fall back to a dev user. This lets Member 3 build the realtime layer before
Member 1's full JWT validation lands.

WebSocket clients pass the token as a query parameter `?token=...` because
browsers don't allow custom headers on the WebSocket handshake. When Member 1
tightens validation, only the signature verification step changes — the
shape of CurrentUser is identical to the HTTP path.
"""
from __future__ import annotations

import os
from typing import Optional

import jwt as pyjwt
from fastapi import WebSocket, status

from backend.models.user import CurrentUser

SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET", "dev-secret")
ALGORITHM = "HS256"


def _decode(token: str, verify: bool = False) -> Optional[dict]:
    """Decode a JWT; returns payload dict or None."""
    try:
        if verify:
            return pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return pyjwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None


async def authenticate_ws(websocket: WebSocket) -> Optional[CurrentUser]:
    """
    Authenticate a WebSocket handshake.

    1. Accept only if a ``token`` query param is present *and* decodable.
    2. If ``ALLOW_DEV_WS_FALLBACK=1``, fall back to a dev identity (useful
       for local demos without a logged-in user).
    3. Otherwise close with 4401 and return None.

    Returns CurrentUser or None (connection already closed).
    """
    token = websocket.query_params.get("token", "")
    payload = _decode(token) if token else None

    if payload and payload.get("sub"):
        return CurrentUser(
            id=str(payload.get("sub")),
            email=payload.get("email", "unknown@example.com"),
            username=payload.get("username", "unknown"),
        )

    if os.getenv("ALLOW_DEV_WS_FALLBACK", "1") == "1":
        return CurrentUser(
            id="dev-user-001",
            email="dev@example.com",
            username="devuser",
        )

    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth required")
    return None
