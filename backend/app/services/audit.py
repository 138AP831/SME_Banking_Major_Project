"""Audit trail helper. Every consequential action writes one AuditEvent."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.util import scrub_for_logs, to_jsonable
from app.models.entities import AuditEvent, new_id


def log_audit(db: Session, *, actor: str, role: str, action: str, category: str, entity_type: str, entity_id: str, summary: str,
              sme_id: str | None = None, run_id: str | None = None, details: dict[str, Any] | None = None, severity: str = "info",
              is_ai: bool = False) -> str:
    ev = AuditEvent(id=new_id("AUD"), actor=actor, role=role, action=action, category=category, entity_type=entity_type, entity_id=entity_id,
                    summary=scrub_for_logs(summary), sme_id=sme_id, run_id=run_id, details=to_jsonable(details or {}), severity=severity, is_ai_activity=is_ai)
    db.add(ev)
    db.flush()
    return ev.id
