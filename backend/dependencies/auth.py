"""
Auth dependency stub — Member 1 will replace this with real JWT validation.
Until then, reads Authorization header and decodes without verification,
or falls back to a dev user so Member 2 can develop independently.
"""
from fastapi import Header
from backend.models.user import CurrentUser

import jwt as pyjwt


async def get_current_user(authorization: str = Header(default="")) -> CurrentUser:
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        try:
            payload = pyjwt.decode(token, options={"verify_signature": False})
            return CurrentUser(
                id=payload.get("sub", "unknown"),
                email=payload.get("email", "unknown@example.com"),
                username=payload.get("username", "unknown"),
            )
        except Exception:
            pass

    # Dev fallback — remove when Member 1 wires real JWT
    return CurrentUser(
        id="dev-user-001",
        email="dev@example.com",
        username="devuser",
    )
