"""
Authentication dependency.

This entire module is the swap point for OIDC (Step 9). The contract
`require_token(...) -> User` stays the same; the implementation will change
to validate an OIDC token and look up / create the user.
"""

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import DEV_USER_ID, User, get_db


def require_token(
    hardad_token: str | None = Cookie(None),
    db: DBSession = Depends(get_db),
) -> User:
    """Validate the demo access token and return the dev user."""
    if hardad_token is None or hardad_token != settings.DEMO_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    user = db.get(User, DEV_USER_ID)
    if user is None:
        raise HTTPException(status_code=500, detail="Dev user not seeded")
    return user
