"""Human-in-the-loop review queue: creation, role/business-rule checks, decision effects."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.util import to_jsonable
from app.models.entities import SME, Alert, CreditApplication, HumanReviewDecision, KYCApplication, WorkflowRun, new_id, utcnow
from app.repositories import data as repo
from app.services.audit import log_audit
from app.services.policy import ALLOWED_ROLES

ACTIONS = ("approve", "reject", "request_info")
ACTION_LABEL = {
    "kyc_exception": {"approve": "Approve KYC (override exceptions)", "reject": "Reject KYC", "request_info": "Request more information"},
    "credit_referral": {"approve": "Recommend to proceed", "reject": "Decline", "request_info": "Request more information"},
    "risk_escalation": {"approve": "Escalate to risk committee", "reject": "Dismiss alert", "request_info": "Keep under review"},
}
MIN_REASON = 10
MIN_OVERRIDE_REASON = 25


class ReviewError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def create_pending(db: Session, *, run_id: str | None, sme_id: str, kind: str, reasons: list[dict], evidence: dict | None = None, subject_id: str | None = None) -> HumanReviewDecision:
    """Idempotent: one pending item per (sme, kind, run)."""
    existing = db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.sme_id == sme_id, HumanReviewDecision.review_kind == kind,
                                                            HumanReviewDecision.status == "pending", HumanReviewDecision.run_id == run_id)).first()
    if existing:
        return existing
    subject_type = {"kyc_exception": "kyc", "credit_referral": "credit", "risk_escalation": "alert"}[kind]
    if subject_id is None:
        if kind == "kyc_exception":
            a = repo.latest_kyc(db, sme_id)
            subject_id = a.id if a else sme_id
        elif kind == "credit_referral":
            ca = repo.open_credit_application(db, sme_id)
            subject_id = ca.id if ca else sme_id
        else:
            al = repo.active_alert(db, sme_id)
            subject_id = al.id if al else sme_id
    row = HumanReviewDecision(id=new_id("REV"), run_id=run_id, sme_id=sme_id, subject_type=subject_type, subject_id=subject_id, review_kind=kind, status="pending",
                              trigger_reasons=to_jsonable(reasons), evidence=to_jsonable(evidence or {}), allowed_roles=ALLOWED_ROLES[kind])
    db.add(row)
    db.flush()
    sme = db.get(SME, sme_id)
    log_audit(db, actor="SMEFlow AI", role="system", action="human_review_requested", category="review", entity_type="review", entity_id=row.id, sme_id=sme_id, run_id=run_id,
              summary=f"Human review required for {sme.name}: {kind.replace('_', ' ')} ({len(reasons)} trigger(s))", severity="attention",
              details={"kind": kind, "rules": [r.get("rule_id") for r in reasons]}, is_ai=True)
    return row


def _validate(db: Session, row: HumanReviewDecision, action: str, reason: str, role: str) -> None:
    if row.status != "pending":
        raise ReviewError("This review has already been decided.", 409)
    if action not in ACTIONS:
        raise ReviewError(f"Unknown action '{action}'.")
    if role not in (row.allowed_roles or []):
        raise ReviewError(f"The role '{role}' is not permitted to decide a {row.review_kind.replace('_', ' ')} review. Allowed: {', '.join(row.allowed_roles)}.", 403)
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise ReviewError(f"A reason of at least {MIN_REASON} characters is required for the audit trail.")
    if row.review_kind == "kyc_exception" and action == "approve":
        app = db.get(KYCApplication, row.subject_id)
        if app:
            if app.missing_documents:
                raise ReviewError("KYC cannot be approved while required documents are missing: " + ", ".join(app.missing_documents) + ". Request information instead.")
            blocking = [e for e in (app.exceptions or []) if e.get("severity") == "blocking"]
            if blocking and len(reason) < MIN_OVERRIDE_REASON:
                raise ReviewError(f"Approving over {len(blocking)} blocking exception(s) is an override and needs a documented reason of at least {MIN_OVERRIDE_REASON} characters.")
    if row.review_kind == "credit_referral" and action == "approve":
        ca = db.get(CreditApplication, row.subject_id)
        if ca and ca.status == "not_eligible":
            raise ReviewError("The policy outcome is 'not eligible'; a recommendation to proceed cannot be recorded.")


def decide(db: Session, review_id: str, action: str, reason: str, role: str, reviewer: str) -> dict[str, Any]:
    row = db.get(HumanReviewDecision, review_id)
    if not row:
        raise ReviewError("Review item not found.", 404)
    _validate(db, row, action, reason, role)
    sme = db.get(SME, row.sme_id)
    prev = new = None
    if row.review_kind == "kyc_exception":
        app = db.get(KYCApplication, row.subject_id) or repo.latest_kyc(db, row.sme_id)
        if app:
            prev = app.status
            new = {"approve": "approved", "reject": "rejected", "request_info": "info_requested"}[action]
            app.status = new
            app.updated_at = utcnow()
            app.timeline = [*(app.timeline or []), {"ts": utcnow().isoformat(timespec="seconds"), "event": f"review_{action}", "detail": reason.strip(), "actor": f"{reviewer} ({role})"}]
            if new == "approved":
                sme.onboarding_status = "active"
                sme.segment = "client"
    elif row.review_kind == "credit_referral":
        ca = db.get(CreditApplication, row.subject_id) or repo.open_credit_application(db, row.sme_id)
        if ca:
            prev = ca.status
            new = {"approve": "recommended_to_proceed", "reject": "declined_by_reviewer", "request_info": "info_requested"}[action]
            ca.status = new
            ca.updated_at = utcnow()
    else:
        al = db.get(Alert, row.subject_id) or repo.active_alert(db, row.sme_id)
        if al:
            prev = al.review_status
            new = {"approve": "escalated", "reject": "dismissed", "request_info": "in_review"}[action]
            al.review_status = new
            al.updated_at = utcnow()
    row.status = "decided"
    row.action = action
    row.reason = reason.strip()
    row.reviewer_role = role
    row.reviewer_name = reviewer
    row.previous_status = prev
    row.new_status = new
    row.decided_at = utcnow()
    row.audit_event_id = log_audit(db, actor=reviewer, role=role, action=f"review_{action}", category="review", entity_type="review", entity_id=row.id, sme_id=row.sme_id, run_id=row.run_id,
                                   summary=f"{role} {ACTION_LABEL[row.review_kind][action].lower()} for {sme.name}: {reason.strip()[:160]}",
                                   severity="info", details={"kind": row.review_kind, "previous_status": prev, "new_status": new, "subject": row.subject_id})
    return {"id": row.id, "run_id": row.run_id, "sme_id": row.sme_id, "kind": row.review_kind, "action": action, "previous_status": prev, "new_status": new,
            "resume_payload": {"review_id": row.id, "action": action, "reason": row.reason, "reviewer": reviewer, "role": role, "kind": row.review_kind}}


def view(db: Session, row: HumanReviewDecision, role: str | None = None) -> dict[str, Any]:
    sme = db.get(SME, row.sme_id)
    run = db.get(WorkflowRun, row.run_id) if row.run_id else None
    return {"id": row.id, "run_id": row.run_id, "template": run.template if run else None, "sme_id": row.sme_id, "sme_name": sme.name, "industry": sme.industry,
            "subject_type": row.subject_type, "subject_id": row.subject_id, "kind": row.review_kind, "status": row.status, "reasons": row.trigger_reasons or [],
            "evidence": row.evidence or {}, "allowed_roles": row.allowed_roles or [], "can_decide": bool(role and role in (row.allowed_roles or [])),
            "action_labels": ACTION_LABEL[row.review_kind], "action": row.action, "reason": row.reason, "reviewer_role": row.reviewer_role, "reviewer_name": row.reviewer_name,
            "previous_status": row.previous_status, "new_status": row.new_status, "created_at": row.created_at.isoformat(timespec="seconds"),
            "decided_at": row.decided_at.isoformat(timespec="seconds") if row.decided_at else None, "exposure": sme.outstanding_exposure}


def queue(db: Session, role: str | None = None, status: str | None = "pending") -> list[dict]:
    q = select(HumanReviewDecision).order_by(HumanReviewDecision.created_at.desc())
    if status:
        q = q.where(HumanReviewDecision.status == status)
    return [view(db, r, role) for r in db.scalars(q)]
