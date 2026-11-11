"""Audit trail helpers. Every action that reads sensitive data or changes state is recorded."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.db import AuditEvent


def record(db: Session, actor: str, action: str, target: str = "", outcome: str = "success",
           details: dict[str, Any] | None = None, commit: bool = True) -> AuditEvent:
    ev = AuditEvent(actor=actor, action=action, target=target, outcome=outcome, details=details or {})
    db.add(ev)
    if commit:
        db.commit()
    return ev


def recent(db: Session, limit: int = 200, actor: str | None = None, action: str | None = None) -> list[AuditEvent]:
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if actor:
        q = q.where(AuditEvent.actor == actor)
    if action:
        q = q.where(AuditEvent.action.like(f"{action}%"))
    return list(db.scalars(q))


def to_dict(ev: AuditEvent) -> dict[str, Any]:
    return {
        "id": ev.id, "ts": ev.ts.isoformat(), "actor": ev.actor, "action": ev.action,
        "target": ev.target, "outcome": ev.outcome, "details": ev.details,
    }
