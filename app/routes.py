"""HTTP routes.

Three public user-facing endpoints back the browser UI:

  - ``POST /messages``   — the tutor turn (budget-checked, scored for CEFR)
  - ``POST /translate``  — EN translation of a phrase or full tutor bubble
  - ``GET  /dict/{word}`` — dictionary lookup (local seed → DB cache → LLM)
  - ``GET  /news``       — three conversation starters tuned to the user's CEFR

Every LLM call is accounted against the caller's daily token budget via
``app.budget.record_usage`` so translation and dictionary calls participate
in the same quota as the main tutor reply.

Admin endpoints under ``/admin/...`` are token-protected via
``require_admin`` — same bearer token mechanism, admin flag required.
"""

import json
import re
import secrets
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.auth import require_admin, require_token
from app.budget import check_budget, record_usage
from app.cefr import record_user_score
from app.config import settings
from app.db import (
    DailyUsage,
    DictCache,
    Message,
    NewsTopic,
    Session,
    User,
    get_db,
)
from app.llm import (
    generate_news_topics,
    lookup_word,
    translate_text,
    tutor_reply,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Static Swedish dictionary seed — loaded once at import, ~300 high-frequency
# A1-B1 words. Misses fall through to the DB cache and then to the LLM.
# ---------------------------------------------------------------------------
_DICT_SEED_PATH = Path(__file__).parent / "static" / "dict_sv.json"
_DICT_SEED: dict[str, dict] = {}
try:
    _seed_doc = json.loads(_DICT_SEED_PATH.read_text())
    _DICT_SEED = {
        k.lower(): v for k, v in (_seed_doc.get("entries") or {}).items()
    }
except FileNotFoundError:
    # Keep the app bootable even if someone runs without the seed file.
    _DICT_SEED = {}


_WORD_CLEAN_RE = re.compile(r"[^\wåäöÅÄÖ-]", re.UNICODE)


def _normalize_word(raw: str) -> str:
    """Lowercase and strip surrounding punctuation from a dictionary query."""
    return _WORD_CLEAN_RE.sub("", raw.strip()).lower()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class MessageRequest(BaseModel):
    session_id: str | None = None
    content: str


class TranslateRequest(BaseModel):
    text: str


class CreateUserRequest(BaseModel):
    name: str
    daily_token_budget: int | None = Field(default=50000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _remaining_for(user: User, db: DBSession) -> int | None:
    """Compute remaining tokens for today. ``None`` = unlimited (admin)."""
    if user.daily_token_budget is None:
        return None
    row = db.execute(
        select(DailyUsage).where(
            DailyUsage.user_id == user.id,
            DailyUsage.date == date.today(),
        )
    ).scalar_one_or_none()
    used = (row.tokens_in + row.tokens_out) if row else 0
    return max(user.daily_token_budget - used, 0)


def _user_cefr_payload(user: User) -> dict:
    """Current CEFR state for a user, safe for JSON serialization."""
    return {
        "avg": round(user.cefr_score_avg, 1) if user.cefr_score_avg is not None else None,
        "level": user.cefr_level,
        "samples_count": len(user.cefr_samples or []),
    }


# ---------------------------------------------------------------------------
# Unauthenticated / UI shell
# ---------------------------------------------------------------------------


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


_LOGIN_HTML = (
    "<html><body style='background:#1a1a2e;color:#e0e0e0;font-family:system-ui;'>"
    "<div style='max-width:400px;margin:100px auto;text-align:center;'>"
    "<h2>Härdad</h2>"
    "{notice}"
    "<form method='get'>"
    "<input name='token' placeholder='Paste your access token' "
    "style='width:100%;padding:8px;margin:8px 0;background:#16213e;color:#e0e0e0;border:1px solid #444;'>"
    "<button type='submit' style='padding:8px 16px;'>Log in</button>"
    "</form></div></body></html>"
)


def _login_response(notice_html: str = "", *, clear_cookie: bool = False):
    """Render the login form. Optionally clear a stale `tala_token` cookie."""
    body = _LOGIN_HTML.format(notice=notice_html)
    response = HTMLResponse(body)
    if clear_cookie:
        response.delete_cookie("tala_token")
    return response


@router.get("/", response_class=HTMLResponse)
def chat_page(
    request: Request,
    token: str | None = None,
    tala_token: str | None = Cookie(None),
    db: DBSession = Depends(get_db),
):
    # If token provided as query param, set cookie and redirect.
    if token is not None:
        response = RedirectResponse(url=request.scope.get("root_path", "") + "/")
        response.set_cookie("tala_token", token, httponly=True, samesite="lax")
        return response

    # No cookie → plain login form.
    if tala_token is None:
        return _login_response()

    # Cookie present → validate. A stale/revoked token (e.g. the user was
    # deleted since last visit) should self-heal: drop the cookie and show
    # the login form with an explanatory notice instead of a dead 401 page.
    user = db.execute(
        select(User).where(User.access_token == tala_token)
    ).scalar_one_or_none()
    if user is None:
        return _login_response(
            notice_html=(
                "<p style='color:#f04747;font-size:13px;margin:0 0 12px;'>"
                "Your previous session is no longer valid. Please log in again."
                "</p>"
            ),
            clear_cookie=True,
        )

    # The React shell pulls everything else through /me, /messages, etc.
    return templates.TemplateResponse(
        request,
        name="chat.html",
        context={
            "user_name": user.name or "Elev",
        },
    )


# ---------------------------------------------------------------------------
# Authenticated user endpoints
# ---------------------------------------------------------------------------


@router.get("/me")
def me(
    user: User = Depends(require_token),
    db: DBSession = Depends(get_db),
):
    """Bootstrap payload for the UI: who you are, your CEFR, your budget.

    Also returns the most-recent open session's messages so the chat
    pane can repopulate on reload.
    """
    stmt = (
        select(Session)
        .where(Session.user_id == user.id, Session.ended_at.is_(None))
        .order_by(Session.started_at.desc())
        .limit(1)
    )
    current_session = db.execute(stmt).scalar_one_or_none()

    messages_out: list[dict] = []
    session_id: str | None = None
    if current_session is not None:
        session_id = str(current_session.id)
        msg_stmt = (
            select(Message)
            .where(Message.session_id == current_session.id)
            .order_by(Message.created_at.asc())
        )
        for m in db.execute(msg_stmt).scalars().all():
            messages_out.append(
                {
                    "id": str(m.id),
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat(),
                    "cefr_score": m.cefr_score,
                    "corrections": m.corrections or [],
                }
            )

    return {
        "user": {
            "id": str(user.id),
            "name": user.name,
            "is_admin": user.is_admin,
        },
        "cefr": _user_cefr_payload(user),
        "budget": {
            "daily_token_budget": user.daily_token_budget,
            "remaining": _remaining_for(user, db),
        },
        "session_id": session_id,
        "messages": messages_out,
    }


@router.post("/messages")
def post_message(
    body: MessageRequest,
    user: User = Depends(require_token),
    db: DBSession = Depends(get_db),
):
    """Tutor turn — one LLM call produces reply + CEFR score + corrections.

    Budget is checked up-front; on success, the response includes the new
    rolling-average CEFR level so the UI's progress bar can animate.
    """
    # Budget check BEFORE the expensive Anthropic call.
    check_budget(user, db)

    # Resolve or create session
    if body.session_id:
        try:
            session_id = uuid.UUID(body.session_id)
        except ValueError:
            return JSONResponse({"error": "Invalid session"}, status_code=400)
        session = db.get(Session, session_id)
        if session is None or session.user_id != user.id:
            return JSONResponse({"error": "Invalid session"}, status_code=400)
    else:
        session = Session(user_id=user.id)
        db.add(session)
        db.flush()
        session_id = session.id

    # Save user message — cefr_score/corrections filled in after the LLM call
    user_msg = Message(session_id=session_id, role="user", content=body.content)
    db.add(user_msg)
    db.flush()

    # Fetch recent history for the API context
    limit = settings.HISTORY_RETENTION_TURNS * 2
    history_stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    history_rows = list(reversed(db.execute(history_stmt).scalars().all()))
    api_messages = [{"role": m.role, "content": m.content} for m in history_rows]

    # Structured tutor call — returns {reply, cefr_score, corrections}
    parsed, tokens_in, tokens_out = tutor_reply(api_messages)
    reply_text: str = parsed.get("reply") or ""
    cefr_score: float = float(parsed.get("cefr_score") or 0.0)
    corrections: list = parsed.get("corrections") or []

    # Attach the CEFR score + corrections to the *user* message that
    # triggered them — makes post-hoc analytics trivial.
    user_msg.cefr_score = cefr_score
    user_msg.corrections = corrections

    # Save assistant message + record usage in the same transaction
    assistant_msg = Message(
        session_id=session_id,
        role="assistant",
        content=reply_text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    db.add(assistant_msg)

    # Roll the average forward and persist the updated level on the user row.
    avg, level = record_user_score(user, cefr_score, db)

    record_usage(user, tokens_in, tokens_out, db)
    db.commit()

    return {
        "session_id": str(session_id),
        "reply": reply_text,
        "cefr": {
            "score": round(cefr_score, 1),
            "avg": round(avg, 1),
            "level": level,
            "samples_count": len(user.cefr_samples or []),
        },
        "corrections": corrections,
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "remaining": _remaining_for(user, db),
        },
    }


@router.post("/translate")
def post_translate(
    body: TranslateRequest,
    user: User = Depends(require_token),
    db: DBSession = Depends(get_db),
):
    """Translate Swedish text to English. Budgeted against the user."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Text too long (max 2000 chars)")

    check_budget(user, db)

    english, tokens_in, tokens_out = translate_text(text)
    record_usage(user, tokens_in, tokens_out, db)
    db.commit()

    return {
        "translation": english,
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "remaining": _remaining_for(user, db),
        },
    }


@router.get("/dict/{word}")
def get_dict(
    word: str,
    user: User = Depends(require_token),
    db: DBSession = Depends(get_db),
):
    """Swedish dictionary lookup.

    Resolution order:
      1. In-memory seed file (~300 high-frequency words, free)
      2. ``dict_cache`` table (previously resolved via LLM, free)
      3. Haiku call with the result cached for next time (budgeted)
    """
    key = _normalize_word(word)
    if not key:
        raise HTTPException(status_code=400, detail="Invalid word")

    seed_entry = _DICT_SEED.get(key)
    if seed_entry is not None:
        return {
            "word": key,
            "entry": seed_entry,
            "source": "seed",
            "usage": {
                "tokens_in": 0,
                "tokens_out": 0,
                "remaining": _remaining_for(user, db),
            },
        }

    cached = db.get(DictCache, key)
    if cached is not None:
        return {
            "word": key,
            "entry": cached.entry,
            "source": "cache",
            "usage": {
                "tokens_in": 0,
                "tokens_out": 0,
                "remaining": _remaining_for(user, db),
            },
        }

    # Cache miss — charge the call to the user's budget.
    check_budget(user, db)
    entry, tokens_in, tokens_out = lookup_word(key)

    # Best-effort upsert. Race with another request for the same word is
    # rare and benign — last writer wins.
    existing = db.get(DictCache, key)
    if existing is None:
        db.add(DictCache(word=key, entry=entry))
    else:
        existing.entry = entry

    record_usage(user, tokens_in, tokens_out, db)
    db.commit()

    return {
        "word": key,
        "entry": entry,
        "source": "llm",
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "remaining": _remaining_for(user, db),
        },
    }


@router.get("/news")
def get_news(
    user: User = Depends(require_token),
    db: DBSession = Depends(get_db),
):
    """Three conversation-starter topic cards tuned to the user's CEFR level.

    Cached per (date, cefr_level) — the first user at a given level pays for
    that day's generation; the rest get it for free. Users without enough
    CEFR history yet (<3 samples) see the A2 set.
    """
    level = user.cefr_level
    if not level or len(user.cefr_samples or []) < 3:
        level = "A2"

    today = date.today()
    cached = db.execute(
        select(NewsTopic).where(
            NewsTopic.date == today,
            NewsTopic.cefr_level == level,
        )
    ).scalar_one_or_none()

    if cached is not None:
        return {
            "cefr_level": level,
            "topics": cached.topics,
            "source": "cache",
            "usage": {
                "tokens_in": 0,
                "tokens_out": 0,
                "remaining": _remaining_for(user, db),
            },
        }

    check_budget(user, db)
    topics, tokens_in, tokens_out = generate_news_topics(level)

    # Empty topics list means the model misbehaved — don't cache garbage,
    # but still record the tokens consumed.
    if topics:
        row = NewsTopic(date=today, cefr_level=level, topics=topics)
        db.add(row)

    record_usage(user, tokens_in, tokens_out, db)
    db.commit()

    return {
        "cefr_level": level,
        "topics": topics,
        "source": "llm",
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "remaining": _remaining_for(user, db),
        },
    }


# ---------------------------------------------------------------------------
# Admin endpoints — protected by require_admin. CLI/curl only.
# ---------------------------------------------------------------------------


def _today_usage_for(user: User, db: DBSession) -> tuple[int, int]:
    row = db.execute(
        select(DailyUsage).where(
            DailyUsage.user_id == user.id,
            DailyUsage.date == date.today(),
        )
    ).scalar_one_or_none()
    if row is None:
        return 0, 0
    return row.tokens_in, row.tokens_out


@router.post("/admin/users", status_code=201)
def admin_create_user(
    body: CreateUserRequest,
    _admin: User = Depends(require_admin),
    db: DBSession = Depends(get_db),
):
    """Create a new non-admin user with a freshly-generated access token."""
    token = secrets.token_urlsafe(32)
    new_user = User(
        name=body.name,
        access_token=token,
        is_admin=False,
        daily_token_budget=body.daily_token_budget,
    )
    db.add(new_user)
    db.commit()
    return {
        "name": new_user.name,
        "access_token": token,
        "daily_token_budget": new_user.daily_token_budget,
    }


@router.get("/admin/users")
def admin_list_users(
    _admin: User = Depends(require_admin),
    db: DBSession = Depends(get_db),
):
    """List all users with today's usage. Access tokens are never returned."""
    users = db.execute(select(User).order_by(User.created_at.asc())).scalars().all()
    out = []
    for u in users:
        tokens_in, tokens_out = _today_usage_for(u, db)
        if u.daily_token_budget is None:
            remaining: int | None = None
        else:
            remaining = max(u.daily_token_budget - (tokens_in + tokens_out), 0)
        out.append(
            {
                "id": str(u.id),
                "name": u.name,
                "is_admin": u.is_admin,
                "daily_token_budget": u.daily_token_budget,
                "cefr_level": u.cefr_level,
                "cefr_score_avg": (
                    round(u.cefr_score_avg, 1)
                    if u.cefr_score_avg is not None
                    else None
                ),
                "usage": {
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "remaining": remaining,
                },
            }
        )
    return out


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: DBSession = Depends(get_db),
):
    """Delete a non-admin user along with their daily usage rows."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete admin users")

    db.execute(
        DailyUsage.__table__.delete().where(DailyUsage.user_id == target.id)
    )
    db.delete(target)
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.get("/admin/usage")
def admin_usage_summary(
    _admin: User = Depends(require_admin),
    db: DBSession = Depends(get_db),
):
    """Today's token usage across all users, plus a list of users over 80%."""
    today = date.today()
    users = db.execute(select(User)).scalars().all()
    rows = db.execute(
        select(DailyUsage).where(DailyUsage.date == today)
    ).scalars().all()
    usage_by_user = {r.user_id: r for r in rows}

    total_in = 0
    total_out = 0
    per_user = []
    over_80 = []

    for u in users:
        row = usage_by_user.get(u.id)
        tokens_in = row.tokens_in if row else 0
        tokens_out = row.tokens_out if row else 0
        total_in += tokens_in
        total_out += tokens_out

        if u.daily_token_budget is None:
            remaining: int | None = None
            pct: float | None = None
        else:
            used = tokens_in + tokens_out
            remaining = max(u.daily_token_budget - used, 0)
            pct = (used / u.daily_token_budget) if u.daily_token_budget > 0 else 0.0

        entry = {
            "id": str(u.id),
            "name": u.name,
            "is_admin": u.is_admin,
            "daily_token_budget": u.daily_token_budget,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "remaining": remaining,
        }
        per_user.append(entry)

        if pct is not None and pct >= 0.8:
            over_80.append({**entry, "percent_used": round(pct * 100, 1)})

    return {
        "date": today.isoformat(),
        "totals": {
            "tokens_in": total_in,
            "tokens_out": total_out,
            "total": total_in + total_out,
        },
        "per_user": per_user,
        "over_80_percent": over_80,
    }
