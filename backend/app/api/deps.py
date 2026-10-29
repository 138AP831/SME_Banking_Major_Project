from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db

ROLES = ["Relationship Manager", "Credit Analyst", "KYC Analyst", "Risk Manager", "SME Customer"]
DEFAULT_USER = {"Relationship Manager": "Alex Morgan", "Credit Analyst": "Dev Krishnan", "KYC Analyst": "Isha Rao", "Risk Manager": "Marcus Fernandes", "SME Customer": "SME portal user"}
STAFF = ROLES[:4]


@dataclass
class Ctx:
    role: str
    user: str

    @property
    def can_reveal(self) -> bool:
        return self.role in ("KYC Analyst", "Credit Analyst")


def get_ctx(x_demo_role: str = Header(default="Relationship Manager"), x_demo_user: str | None = Header(default=None)) -> Ctx:
    if x_demo_role not in ROLES:
        raise HTTPException(400, f"Unknown role '{x_demo_role}'")
    return Ctx(role=x_demo_role, user=x_demo_user or DEFAULT_USER[x_demo_role])


def staff(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if ctx.role not in STAFF:
        raise HTTPException(403, "This area is not available to the SME Customer role.")
    return ctx


def require_roles(*roles: str):
    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        if ctx.role not in roles:
            raise HTTPException(403, f"The role '{ctx.role}' cannot perform this action. Allowed: {', '.join(roles)}.")
        return ctx
    return dep


__all__ = ["Ctx", "get_ctx", "staff", "require_roles", "get_db", "Session", "ROLES", "STAFF", "DEFAULT_USER"]
