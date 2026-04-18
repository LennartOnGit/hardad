"""
Daily per-user token budgeting.

Functions here never commit — they operate on the caller's transaction so
budget accounting and message storage succeed or fail together.
"""

from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.db import DailyUsage, User


def _quota_exceeded_response() -> HTTPException:
    """Raise a 429 with a Swedish error body."""
    return HTTPException(
        status_code=429,
        detail={
            "error": "Du har använt dagens kvot — försök igen i morgon.",
            "remaining": 0,
        },
    )


def check_budget(user: User, db: DBSession) -> int | None:
    """Return remaining tokens for today, or None if the user is unlimited.

    Raises HTTPException(429) when the user has already spent their daily
    budget.
    """
    if user.daily_token_budget is None:
        return None

    today = date.today()
    row = db.execute(
        select(DailyUsage).where(
            DailyUsage.user_id == user.id,
            DailyUsage.date == today,
        )
    ).scalar_one_or_none()

    used = (row.tokens_in + row.tokens_out) if row is not None else 0
    remaining = user.daily_token_budget - used
    if remaining <= 0:
        raise _quota_exceeded_response()
    return remaining


def record_usage(
    user: User, tokens_in: int, tokens_out: int, db: DBSession
) -> None:
    """Add `tokens_in`/`tokens_out` to today's usage row for `user`.

    Creates the row if it does not yet exist. Does NOT commit — the caller's
    transaction owns the commit.
    """
    today = date.today()
    row = db.execute(
        select(DailyUsage).where(
            DailyUsage.user_id == user.id,
            DailyUsage.date == today,
        )
    ).scalar_one_or_none()

    if row is None:
        row = DailyUsage(
            user_id=user.id,
            date=today,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        db.add(row)
    else:
        row.tokens_in += tokens_in
        row.tokens_out += tokens_out
