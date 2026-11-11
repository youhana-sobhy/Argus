"""JSON API. Every route is RBAC-protected and state changes are audited."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus import __version__
from argus.ai.assistant import Assistant
from argus.ai.providers import ProviderError, build_provider
from argus.changes import engine
from argus.config import get_settings
from argus.connectors import aci_mo
from argus.core import audit
from argus.core.inventory import get_inventory
from argus.core.security import Principal, authenticate, current_user, require
from argus.db import ChangeRequest, get_db

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- auth / system
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = authenticate(db, body.username.strip().lower(), body.password)
    if not user:
        audit.record(db, body.username[:64] or "?", "auth.login", outcome="failure")
        raise HTTPException(401, "Wrong username or password")
    request.session.clear()
    request.session["user"] = user.username
    audit.record(db, user.username, "auth.login")
    return {"ok": True, "user": Principal(user.username, user.full_name, user.role).as_dict()}


@router.post("/auth/logout")
def logout(request: Request, user: Principal = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    audit.record(db, user.username, "auth.logout")
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me")
def me(user: Principal = Depends(current_user)) -> dict[str, Any]:
    return user.as_dict()


def system_info() -> dict[str, Any]:
    s = get_settings()
    inv = get_inventory()
    return {
        "version": __version__,
        "connector": s.connector,
        "target": inv.snapshot().target,
        "live": inv.connector.live,
        "live_execution_allowed": s.allow_live_execution or not inv.connector.live,
        "llm_provider": s.llm_provider,
        "llm_model": s.anthropic_model if s.llm_provider == "anthropic" else (s.azure_deployment if s.llm_provider == "azure" else "offline"),
        "redaction": s.redact,
    }


@router.get("/system")
def system(_: Principal = Depends(current_user)) -> dict[str, Any]:
    return system_info() | {"health": get_inventory().connector.health()}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# ---------------------------------------------------------------- inventory
@router.get("/inventory/snapshot")
def snapshot(_: Principal = Depends(require("inventory.read"))) -> dict[str, Any]:
    snap = get_inventory().snapshot()
    return snap.model_dump(mode="json") | {"stats": snap.stats()}


@router.post("/inventory/sync")
def sync(user: Principal = Depends(require("inventory.sync")), db: Session = Depends(get_db)) -> dict[str, Any]:
    snap = get_inventory().snapshot(refresh=True)
    audit.record(db, user.username, "inventory.sync", snap.target, details=snap.stats())
    return {"ok": True, "collected_at": snap.collected_at.isoformat(), "stats": snap.stats()}


@router.get("/inventory/export")
def export(format: str = "normalized", user: Principal = Depends(require("inventory.export")),
           db: Session = Depends(get_db)) -> Response:
    inv = get_inventory()
    snap = inv.snapshot()
    if format == "apic":
        data: Any = aci_mo.export_apic(snap)
    elif format == "native":
        data = inv.native()
    else:
        format = "normalized"
        data = snap.model_dump(mode="json")
    audit.record(db, user.username, "inventory.export", snap.target, details={"format": format})
    name = f"argus-{snap.target}-{format}-{snap.collected_at:%Y%m%d-%H%M}.json"
    return Response(json.dumps(data, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/topology")
def topology(_: Principal = Depends(require("inventory.read"))) -> dict[str, Any]:
    snap = get_inventory().snapshot()
    logical = []
    for t in snap.tenants:
        logical.append({
            "tenant": t.name, "descr": t.descr,
            "vrfs": [{"name": v.name, "bds": [
                {"name": b.name, "subnets": [s.gateway for s in b.subnets],
                 "epgs": [{"name": e.name, "ap": e.app_profile} for e in t.epgs() if e.bridge_domain == b.name]}
                for b in t.bridge_domains if b.vrf == v.name]} for v in t.vrfs],
            "l3outs": [{"name": o.name, "vrf": o.vrf} for o in t.l3outs],
        })
    return {"target": snap.target, "nodes": [n.model_dump() for n in snap.nodes],
            "links": [l.model_dump() for l in snap.links], "logical": logical}


# ---------------------------------------------------------------- chat
class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list)


@router.post("/chat")
def chat(body: ChatIn, user: Principal = Depends(require("chat.use")), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    try:
        provider = build_provider(s)
    except ProviderError as exc:
        provider = None
        note = str(exc)
    else:
        note = ""
    assistant = Assistant(provider, redact=s.redact)
    result = assistant.ask(body.question, body.history, get_inventory().snapshot())
    if note:
        result["answer"] = f"*{note} Answering offline instead.*\n\n" + result["answer"]
    audit.record(db, user.username, "chat.ask", result.get("provider", ""), details={
        "question": body.question[:500], "provider": result.get("provider"), "masked_values": result.get("masked", 0),
        "proposal": bool(result.get("proposal")),
    })
    return result


# ---------------------------------------------------------------- changes
class ChangeIn(BaseModel):
    intent: dict[str, Any]
    justification: str = ""


class ActionIn(BaseModel):
    note: str = ""


def _err(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/changes")
def list_changes(_: Principal = Depends(require("change.read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(ChangeRequest).order_by(ChangeRequest.id.desc()).limit(200))
    return [engine.to_dict(cr, with_plan=False) for cr in rows]


@router.post("/changes")
def create_change(body: ChangeIn, user: Principal = Depends(require("change.create")), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        cr = engine.create(db, get_inventory(), user, body.intent, body.justification)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc
    except engine.ChangeError as exc:
        raise _err(exc) from exc
    return engine.to_dict(cr)


@router.get("/changes/{change_id}")
def get_change(change_id: int, _: Principal = Depends(require("change.read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        cr = engine.get(db, change_id)
    except engine.ChangeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return engine.to_dict(cr) | {"events": [
        {"ts": e.ts.isoformat(), "actor": e.actor, "from": e.from_status, "to": e.to_status, "note": e.note}
        for e in engine.events(db, change_id)
    ]}


ACTION_PERMS = {
    "dry-run": "change.create", "submit": "change.create", "approve": "change.approve",
    "reject": "change.approve", "execute": "change.execute", "rollback": "change.rollback",
}


@router.post("/changes/{change_id}/{action}")
def change_action(change_id: int, action: str, body: ActionIn, request: Request,
                  user: Principal = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    perm = ACTION_PERMS.get(action)
    if perm is None:
        raise HTTPException(404, f"Unknown action {action}")
    if not user.can(perm):
        audit.record(db, user.username, f"change.{action}", f"CR-{change_id:04d}", "denied", {"reason": "rbac"})
        raise HTTPException(403, f"Your role '{user.role}' cannot {action} changes")
    inv = get_inventory()
    try:
        cr = engine.get(db, change_id)
        if action == "dry-run":
            engine.dry_run(db, inv, user, cr)
        elif action == "submit":
            engine.submit(db, user, cr, body.note)
        elif action == "approve":
            engine.approve(db, user, cr, body.note)
        elif action == "reject":
            engine.reject(db, user, cr, body.note)
        elif action == "execute":
            engine.execute(db, inv, user, cr)
        elif action == "rollback":
            engine.rollback(db, inv, user, cr, body.note)
    except engine.ChangeError as exc:
        raise _err(exc) from exc
    return get_change(change_id, user, db)


# ---------------------------------------------------------------- audit / admin
@router.get("/audit")
def audit_log(limit: int = 300, actor: str | None = None, action: str | None = None,
              _: Principal = Depends(require("audit.read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [audit.to_dict(e) for e in audit.recent(db, min(limit, 1000), actor, action)]


@router.post("/admin/lab-reset")
def lab_reset(user: Principal = Depends(require("admin.lab_reset")), db: Session = Depends(get_db)) -> dict[str, bool]:
    inv = get_inventory()
    reset = getattr(inv.connector, "reset", None)
    if reset is None:
        raise HTTPException(400, "Only the mock lab can be reset")
    reset()
    inv.invalidate()
    audit.record(db, user.username, "admin.lab_reset", inv.connector.target)
    return {"ok": True}
