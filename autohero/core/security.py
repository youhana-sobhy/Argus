"""Authentication and role-based access control.

Demo: local users with PBKDF2-SHA256 password hashes and a signed session cookie.
SSO-ready: :func:`current_user` is the only place that resolves identity, so an
OIDC / Entra ID / LDAP provider can be added without touching the routes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from autohero.db import User, get_db

ROLES = ("viewer", "engineer", "approver", "admin")

#: permission -> roles allowed
PERMISSIONS: dict[str, set[str]] = {
    "inventory.read": {"viewer", "engineer", "approver", "admin"},
    "inventory.export": {"engineer", "approver", "admin"},
    "inventory.sync": {"engineer", "approver", "admin"},
    "chat.use": {"viewer", "engineer", "approver", "admin"},
    "change.read": {"viewer", "engineer", "approver", "admin"},
    "change.create": {"engineer", "admin"},
    "change.execute": {"engineer", "admin"},
    "change.approve": {"approver", "admin"},
    "change.rollback": {"engineer", "approver", "admin"},
    "audit.read": {"approver", "admin"},
    "admin.lab_reset": {"admin"},
}

_ITERATIONS = 240_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
    return hmac.compare_digest(test, digest)


@dataclass(frozen=True)
class Principal:
    username: str
    full_name: str
    role: str

    def can(self, permission: str) -> bool:
        return self.role in PERMISSIONS.get(permission, set())

    def as_dict(self) -> dict[str, object]:
        return {
            "username": self.username, "full_name": self.full_name, "role": self.role,
            "permissions": sorted(p for p in PERMISSIONS if self.can(p)),
        }


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user and verify_password(password, user.password_hash):
        return user
    return None


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    username = request.session.get("user")
    if not username:
        return None
    user = db.scalar(select(User).where(User.username == username))
    if not user:
        return None
    return Principal(user.username, user.full_name, user.role)


def current_user(principal: Principal | None = Depends(current_user_optional)) -> Principal:
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return principal


def require(permission: str):
    def dep(user: Principal = Depends(current_user)) -> Principal:
        if not user.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role '{user.role}' cannot perform '{permission}'")
        return user

    return dep


DEMO_USERS = [
    ("admin", "Platform Admin", "admin"),
    ("engineer", "Youhana Engineer", "engineer"),
    ("approver", "Omar Approver", "approver"),
    ("viewer", "Sara Viewer", "viewer"),
]


def seed_users(db: Session, demo_password: str) -> None:
    for username, name, role in DEMO_USERS:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            db.add(User(username=username, full_name=name, role=role, password_hash=hash_password(demo_password)))
        elif user.full_name != name:
            user.full_name = name  # keep demo display names in sync with DEMO_USERS
    db.commit()
