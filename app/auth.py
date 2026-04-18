"""
Authentication dependencies.

Tokens are stored per-user in the `users` table and are looked up on every
request. Browsers authenticate via the `tala_token` cookie; API/curl callers
pass an `Authorization: Bearer <token>` header.
"""

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.db import User, get_db


def _extract_token(
    tala_token: str | None,
    authorization: str | None,
) -> str | None:
    """Return the bearer/cookie token if any is present, else None."""
    if authorization is not None:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    if tala_token:
        return tala_token
    return None


def require_token(
    tala_token: str | None = Cookie(None),
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> User:
    """Resolve the caller's User from a cookie or Authorization header."""
    token = _extract_token(tala_token, authorization)
    if not token:
        raise HTTPException(
            status_code=401, detail="Invalid or missing access token"
        )
    user = db.execute(
        select(User).where(User.access_token == token)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401, detail="Invalid or missing access token"
        )
    return user


def require_admin(user: User = Depends(require_token)) -> User:
    """Require that the authenticated user has the admin flag set."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
