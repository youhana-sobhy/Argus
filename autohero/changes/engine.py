"""Change request lifecycle (state machine).

    draft ──dry-run──▶ validated ──submit──▶ pending_approval ──approve──▶ approved ──execute──▶ completed
       │                  │                        │                                   │            │
       └──▶ validation_failed                      └──reject──▶ rejected               └──▶ failed   └──rollback──▶ rolled_back
                                                                                     (auto-rollback on failed post-checks)

Rules
  * Every change is dry-run first; nothing reaches the target without a passing plan.
  * Four-eyes: the approver must be a different person than the requester.
  * Execution re-plans against the *current* state (drift check) before pushing.
  * Post-checks verify the result on a fresh snapshot; failure triggers automatic rollback.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohero.changes import planner
from autohero.changes.intents import parse_intent
from autohero.connectors.base import ConnectorError
from autohero.core import audit
from autohero.core.inventory import Inventory
from autohero.core.security import Principal
from autohero.db import ChangeEvent, ChangeRequest, utcnow

TRANSITIONS: dict[str, set[str]] = {
    "dry_run": {"draft", "validated", "validation_failed"},
    "submit": {"validated"},
    "approve": {"pending_approval"},
    "reject": {"pending_approval"},
    "execute": {"approved"},
    "rollback": {"completed"},
}


class ChangeError(ValueError):
    pass


def _move(db: Session, cr: ChangeRequest, actor: str, to_status: str, note: str = "") -> None:
    db.add(ChangeEvent(change_id=cr.id, actor=actor, from_status=cr.status, to_status=to_status, note=note))
    cr.status = to_status
    cr.updated_at = utcnow()


def _guard(cr: ChangeRequest, action: str) -> None:
    if cr.status not in TRANSITIONS[action]:
        raise ChangeError(f"Cannot {action.replace('_', '-')} a change that is '{cr.status}'.")


def create(db: Session, inv: Inventory, user: Principal, intent_data: dict[str, Any], justification: str = "") -> ChangeRequest:
    intent = parse_intent(intent_data)
    cr = ChangeRequest(
        title=intent.title(), kind=intent.kind, target=inv.snapshot().target,
        intent=intent.model_dump(mode="json"), status="draft", requested_by=user.username,
        justification=justification,
    )
    db.add(cr)
    db.flush()
    db.add(ChangeEvent(change_id=cr.id, actor=user.username, from_status="", to_status="draft", note="Change request created"))
    audit.record(db, user.username, "change.create", f"CR-{cr.id:04d}", details={"title": cr.title}, commit=False)
    dry_run(db, inv, user, cr, commit=False)
    db.commit()
    return cr


def dry_run(db: Session, inv: Inventory, user: Principal, cr: ChangeRequest, commit: bool = True) -> ChangeRequest:
    _guard(cr, "dry_run")
    intent = parse_intent(cr.intent)
    snap = inv.snapshot(refresh=True)
    plan = planner.plan(intent, snap, inv.native())
    cr.plan = plan
    cr.risk = plan["risk"]
    to = "validated" if plan["passed"] else "validation_failed"
    fails = sum(1 for c in plan["checks"] if c["status"] == "fail")
    warns = sum(1 for c in plan["checks"] if c["status"] == "warn")
    _move(db, cr, user.username, to, f"Dry-run: {len(plan['checks'])} checks, {fails} failed, {warns} warnings")
    audit.record(db, user.username, "change.dry_run", f"CR-{cr.id:04d}", "success" if plan["passed"] else "blocked",
                 {"risk": plan["risk"], "failed": fails, "warnings": warns}, commit=False)
    if commit:
        db.commit()
    return cr


def submit(db: Session, user: Principal, cr: ChangeRequest, note: str = "") -> ChangeRequest:
    _guard(cr, "submit")
    if cr.requested_by != user.username and user.role != "admin":
        raise ChangeError("Only the requester can submit this change for approval.")
    _move(db, cr, user.username, "pending_approval", note or "Submitted for approval")
    audit.record(db, user.username, "change.submit", f"CR-{cr.id:04d}", commit=False)
    db.commit()
    return cr


def approve(db: Session, user: Principal, cr: ChangeRequest, note: str = "") -> ChangeRequest:
    _guard(cr, "approve")
    if cr.requested_by == user.username:
        audit.record(db, user.username, "change.approve", f"CR-{cr.id:04d}", "denied", {"reason": "four-eyes"})
        raise ChangeError("Four-eyes rule: you cannot approve your own change.")
    cr.approved_by = user.username
    _move(db, cr, user.username, "approved", note or "Approved")
    audit.record(db, user.username, "change.approve", f"CR-{cr.id:04d}", details={"note": note}, commit=False)
    db.commit()
    return cr


def reject(db: Session, user: Principal, cr: ChangeRequest, note: str = "") -> ChangeRequest:
    _guard(cr, "reject")
    _move(db, cr, user.username, "rejected", note or "Rejected")
    audit.record(db, user.username, "change.reject", f"CR-{cr.id:04d}", details={"note": note}, commit=False)
    db.commit()
    return cr


def execute(db: Session, inv: Inventory, user: Principal, cr: ChangeRequest) -> ChangeRequest:
    _guard(cr, "execute")
    intent = parse_intent(cr.intent)
    _move(db, cr, user.username, "executing", "Execution started")
    db.commit()

    # Drift check: re-plan against the state right now
    fresh = planner.plan(intent, inv.snapshot(refresh=True), inv.native())
    if not fresh["passed"]:
        _move(db, cr, user.username, "failed", "Aborted: configuration drifted since approval; pre-checks no longer pass")
        cr.result = {"stage": "drift-check", "checks": fresh["checks"]}
        audit.record(db, user.username, "change.execute", f"CR-{cr.id:04d}", "failure", {"stage": "drift-check"}, commit=False)
        db.commit()
        return cr

    try:
        push = inv.connector.apply(fresh["payload"])
    except ConnectorError as exc:
        _move(db, cr, user.username, "failed", f"Target rejected the change: {exc}")
        cr.result = {"stage": "push", "error": str(exc)}
        audit.record(db, user.username, "change.execute", f"CR-{cr.id:04d}", "failure", {"error": str(exc)}, commit=False)
        db.commit()
        return cr

    inv.invalidate()
    post = planner.verify(intent, inv.snapshot(refresh=True))
    ok = all(c["status"] != "fail" for c in post)
    cr.result = {"stage": "post-check", "push": push, "post_checks": post, "mode": push.get("mode")}
    cr.plan = {**(cr.plan or {}), "rollback_payload": fresh["rollback_payload"]}
    if ok:
        _move(db, cr, user.username, "completed", "Executed and verified by post-checks")
        audit.record(db, user.username, "change.execute", f"CR-{cr.id:04d}", details={"mode": push.get("mode")}, commit=False)
    else:
        _rollback_payload(inv, fresh["rollback_payload"])
        _move(db, cr, "autohero", "failed", "Post-checks failed: automatic rollback applied")
        audit.record(db, user.username, "change.execute", f"CR-{cr.id:04d}", "failure", {"stage": "post-check", "auto_rollback": True}, commit=False)
    db.commit()
    return cr


def _rollback_payload(inv: Inventory, payload: dict[str, Any] | None) -> None:
    if payload:
        inv.connector.apply(payload)
        inv.invalidate()


def rollback(db: Session, inv: Inventory, user: Principal, cr: ChangeRequest, note: str = "") -> ChangeRequest:
    _guard(cr, "rollback")
    payload = (cr.plan or {}).get("rollback_payload")
    if not payload:
        raise ChangeError("This change has no rollback payload.")
    try:
        _rollback_payload(inv, payload)
    except ConnectorError as exc:
        audit.record(db, user.username, "change.rollback", f"CR-{cr.id:04d}", "failure", {"error": str(exc)})
        raise ChangeError(f"Rollback failed: {exc}") from exc
    post = planner.verify(parse_intent(cr.intent), inv.snapshot(refresh=True), rolled_back=True)
    cr.result = {**(cr.result or {}), "rollback_checks": post}
    _move(db, cr, user.username, "rolled_back", note or "Rolled back to the previous state")
    audit.record(db, user.username, "change.rollback", f"CR-{cr.id:04d}", commit=False)
    db.commit()
    return cr


def get(db: Session, change_id: int) -> ChangeRequest:
    cr = db.get(ChangeRequest, change_id)
    if cr is None:
        raise ChangeError(f"Change CR-{change_id:04d} not found.")
    return cr


def events(db: Session, change_id: int) -> list[ChangeEvent]:
    return list(db.scalars(select(ChangeEvent).where(ChangeEvent.change_id == change_id).order_by(ChangeEvent.id)))


def to_dict(cr: ChangeRequest, with_plan: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": cr.id, "ref": f"CR-{cr.id:04d}", "title": cr.title, "kind": cr.kind, "target": cr.target,
        "status": cr.status, "risk": cr.risk, "requested_by": cr.requested_by, "approved_by": cr.approved_by,
        "justification": cr.justification, "intent": cr.intent,
        "created_at": cr.created_at.isoformat(), "updated_at": cr.updated_at.isoformat(),
    }
    if with_plan:
        d["plan"] = cr.plan
        d["result"] = cr.result
    return d
