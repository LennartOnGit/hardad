"""One-off: drop the stale devtoken123 seed admin and create 10 test users.

Run with `uv run python scripts/seed_testusers.py`. Prints a markdown table of
the newly-minted access tokens and their daily budgets.
"""

import secrets

from sqlalchemy import select

from app.db import DailyUsage, Message, Session as ChatSession, SessionLocal, User

# Budget ladder: small → large, one None for an unlimited-budget tester.
BUDGETS: list[tuple[str, int | None]] = [
    ("TestUser01",   1_000),
    ("TestUser02",   2_500),
    ("TestUser03",   5_000),
    ("TestUser04",   7_500),
    ("TestUser05",  10_000),
    ("TestUser06",  15_000),
    ("TestUser07",  25_000),
    ("TestUser08",  50_000),
    ("TestUser09", 100_000),
    ("TestUser10",    None),   # unlimited
]


def main() -> None:
    db = SessionLocal()
    try:
        # ---- Drop devtoken123 + everything that FKs back to it ----
        # Order matters: messages → sessions → daily_usage → user.
        stale = db.execute(
            select(User).where(User.access_token == "devtoken123")
        ).scalar_one_or_none()
        if stale is not None:
            session_ids = [
                sid for (sid,) in db.execute(
                    select(ChatSession.id).where(ChatSession.user_id == stale.id)
                ).all()
            ]
            if session_ids:
                db.execute(
                    Message.__table__.delete().where(
                        Message.session_id.in_(session_ids)
                    )
                )
                db.execute(
                    ChatSession.__table__.delete().where(
                        ChatSession.id.in_(session_ids)
                    )
                )
            db.execute(
                DailyUsage.__table__.delete().where(DailyUsage.user_id == stale.id)
            )
            db.delete(stale)
            db.commit()
            print(
                f"# Deleted stale admin user {stale.id} "
                f"({len(session_ids)} session(s) + messages)"
            )
        else:
            print("# No devtoken123 user found — nothing to delete")

        # ---- Create 10 test users ----
        rows = []
        for name, budget in BUDGETS:
            token = secrets.token_urlsafe(32)
            u = User(
                name=name,
                access_token=token,
                is_admin=False,
                daily_token_budget=budget,
            )
            db.add(u)
            rows.append((name, token, budget))
        db.commit()

        # ---- Print table ----
        print()
        print("| Name | Daily token budget | Access token |")
        print("|---|---:|---|")
        for name, token, budget in rows:
            b = "unlimited" if budget is None else f"{budget:,}"
            print(f"| {name} | {b} | `{token}` |")
    finally:
        db.close()


if __name__ == "__main__":
    main()
