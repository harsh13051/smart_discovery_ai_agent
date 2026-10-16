"""SQLAlchemy models, engine setup and a small versioned migration runner."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event, inspect, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

log = logging.getLogger("discovery.db")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    preferences: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    context: Mapped[str] = mapped_column(Text, default="{}")  # last structured preferences
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Message.id",
    )
    __table_args__ = (Index("ix_conv_user_updated", "user_id", "updated_at"),)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # cards + trace JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Favourite(Base):
    __tablename__ = "favourites"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    restaurant_provider_id: Mapped[str] = mapped_column(String(40))  # provider name, e.g. "osm"
    restaurant_id: Mapped[str] = mapped_column(String(80))           # full normalized id
    snapshot: Mapped[str] = mapped_column(Text)                      # normalized record when saved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "restaurant_id", name="uq_fav_user_restaurant"),)


class RestaurantCache(Base):
    """Normalized records seen recently (detail pages, comparison)."""
    __tablename__ = "restaurant_cache"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    data: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HttpCache(Base):
    """Provider response cache (geocoding and search results) with expiry."""
    __tablename__ = "http_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ migrations
def _m1_initial(engine: Engine) -> None:
    Base.metadata.create_all(engine)  # checkfirst: never touches existing tables


def _m2_add_columns_if_missing(engine: Engine) -> None:
    """Additive-only: bring databases created by earlier builds up to date."""
    insp = inspect(engine)
    with engine.begin() as conn:
        if "users" in insp.get_table_names() and "preferences" not in {c["name"] for c in insp.get_columns("users")}:
            conn.execute(text("ALTER TABLE users ADD COLUMN preferences TEXT DEFAULT '{}'"))


MIGRATIONS = [(1, _m1_initial), (2, _m2_add_columns_if_missing)]  # additive only; never destructive


def run_migrations(engine: Engine) -> list[int]:
    SchemaMigration.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        done = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
    applied = []
    for version, fn in MIGRATIONS:
        if version in done:
            continue
        fn(engine)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                {"v": version, "t": utcnow().isoformat()},
            )
        applied.append(version)
        log.info("applied migration %s", version)
    return applied


def make_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def jloads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
