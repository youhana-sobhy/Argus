"""Database models (SQLAlchemy 2.0). SQLite for the demo, Postgres via AUTOHERO_DATABASE_URL."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from autohero.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16))  # viewer | engineer | approver | admin
    password_hash: Mapped[str] = mapped_column(String(256))
    auth_source: Mapped[str] = mapped_column(String(16), default="local")  # local | sso (future)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # create_app_profile | create_bridge_domain | delete_object
    target: Mapped[str] = mapped_column(String(120))
    intent: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    risk: Mapped[str] = mapped_column(String(10), default="low")
    requested_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    justification: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # payload, rollback, diff, checks
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChangeEvent(Base):
    """Timeline entry for a change request (state transitions)."""

    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")


class AuditEvent(Base):
    """Append-only audit log of every security-relevant action."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(200), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="success")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


@lru_cache
def get_engine():
    url = get_settings().database_url
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = _sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def session() -> Session:
    return _sessionmaker()()


def init_db() -> None:
    Base.metadata.create_all(get_engine())
