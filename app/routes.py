import uuid

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.auth import require_token
from app.config import settings
from app.db import Message, Session, User, get_db
from app.llm import call_anthropic, load_system_prompt

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


class MessageRequest(BaseModel):
    session_id: str | None = None
    content: str


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def chat_page(
    request: Request,
    token: str | None = None,
    hardad_token: str | None = Cookie(None),
    db: DBSession = Depends(get_db),
):
    # If token provided as query param, set cookie and redirect
    if token is not None:
        response = RedirectResponse(url=request.scope.get("root_path", "") + "/")
        response.set_cookie(
            "hardad_token", token, httponly=True, samesite="lax"
        )
        return response

    # If no cookie, show a simple token form
    if hardad_token is None:
        return HTMLResponse(
            "<html><body style='background:#1a1a2e;color:#e0e0e0;font-family:system-ui;'>"
            "<div style='max-width:400px;margin:100px auto;text-align:center;'>"
            "<h2>Härdad</h2>"
            "<form method='get'>"
            "<input name='token' placeholder='Paste your access token' "
            "style='width:100%;padding:8px;margin:8px 0;background:#16213e;color:#e0e0e0;border:1px solid #444;'>"
            "<button type='submit' style='padding:8px 16px;'>Log in</button>"
            "</form></div></body></html>"
        )

    # Validate token
    if hardad_token != settings.DEMO_ACCESS_TOKEN:
        return HTMLResponse("Invalid token", status_code=401)

    # Load messages from the most recent open session for this user, if any
    from app.db import DEV_USER_ID

    stmt = (
        select(Session)
        .where(Session.user_id == DEV_USER_ID, Session.ended_at.is_(None))
        .order_by(Session.started_at.desc())
        .limit(1)
    )
    current_session = db.execute(stmt).scalar_one_or_none()

    messages = []
    session_id = None
    if current_session is not None:
        session_id = str(current_session.id)
        msg_stmt = (
            select(Message)
            .where(Message.session_id == current_session.id)
            .order_by(Message.created_at.asc())
        )
        messages = db.execute(msg_stmt).scalars().all()

    return templates.TemplateResponse(
        request,
        name="chat.html",
        context={
            "messages": messages,
            "session_id": session_id,
        },
    )


@router.post("/messages")
def post_message(
    body: MessageRequest,
    user: User = Depends(require_token),
    db: DBSession = Depends(get_db),
):
    # Resolve or create session
    if body.session_id:
        session_id = uuid.UUID(body.session_id)
        session = db.get(Session, session_id)
        if session is None or session.user_id != user.id:
            return JSONResponse({"error": "Invalid session"}, status_code=400)
    else:
        session = Session(user_id=user.id)
        db.add(session)
        db.flush()
        session_id = session.id

    # Save user message
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

    # Call Anthropic
    system_prompt = load_system_prompt()
    reply_text, tokens_in, tokens_out = call_anthropic(api_messages, system_prompt)

    # Save assistant message
    assistant_msg = Message(
        session_id=session_id,
        role="assistant",
        content=reply_text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    db.add(assistant_msg)
    db.commit()

    return {"session_id": str(session_id), "reply": reply_text}
