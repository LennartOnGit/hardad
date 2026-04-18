import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    daily_token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # CEFR bookkeeping. `cefr_samples` is a JSON list of the most recent 20
    # per-user-message scores (0-100). `cefr_score_avg` / `cefr_level` are
    # denormalized for easy display.
    cefr_samples: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cefr_score_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    cefr_level: Mapped[str | None] = mapped_column(String, nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Set on user messages when the tutor scores them (0..100).
    cefr_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Inline corrections produced for a user message — list of
    # {type: "spell"|"gram", original, fix, note}.
    corrections: Mapped[list | None] = mapped_column(JSON, nullable=True)


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_daily_usage_user_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class NewsTopic(Base):
    """Cached news topics per (date, cefr_level). Shared across users at the
    same level — the topics are a function of level, not identity, so one
    generation per day per level is enough.
    """

    __tablename__ = "news_topics"
    __table_args__ = (
        UniqueConstraint("date", "cefr_level", name="uq_news_date_level"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cefr_level: Mapped[str] = mapped_column(String, nullable=False)  # A1..C2
    topics: Mapped[list] = mapped_column(JSON, nullable=False)  # list of 3
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DictCache(Base):
    """Cache of LLM-generated dictionary entries for words that aren't in
    the static seed file. Key is the lowercased Swedish headword.
    """

    __tablename__ = "dict_cache"

    word: Mapped[str] = mapped_column(String, primary_key=True)
    entry: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_db():
    """FastAPI dependency that yields a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables and ensure the admin user exists. Called once at startup."""
    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        admin_token = settings.ADMIN_TOKEN
        existing = db.execute(
            select(User).where(User.access_token == admin_token)
        ).scalar_one_or_none()
        if existing is None:
            admin = User(
                name="Admin",
                access_token=admin_token,
                is_admin=True,
                daily_token_budget=None,
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()
