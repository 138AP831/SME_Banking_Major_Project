from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DEFAULT_USER, ROLES, Ctx, get_ctx, get_db, require_roles, staff
from app.config import get_settings
from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.db import seed as seed_mod
from app.models.entities import (SME, Alert, AppSetting, AuditEvent, CreditApplication, HumanReviewDecision, KYCApplication, ModelMetric, WorkflowRun, utcnow)
from app.repositories import data as repo
from app.services import dashboard, evaluation, llm
from app.services import policy as policy_svc
from app.services.audit import log_audit
from app.services.views import sme_summary

router = APIRouter(prefix="/api")

NAV = {  # which pages each role sees (enforced server-side for the data routes as well)
    "Relationship Manager": ["overview", "workflows", "prospecting", "ews", "portfolio", "kyc", "credit", "cashflow", "reviews", "models", "governance", "settings"],
    "Credit Analyst": ["overview", "workflows", "credit", "cashflow", "portfolio", "kyc", "reviews", "models", "governance", "settings"],
    "KYC Analyst": ["overview", "workflows", "kyc", "portfolio", "reviews", "governance", "settings"],
    "Risk Manager": ["overview", "workflows", "ews", "credit", "cashflow", "portfolio", "reviews", "models", "governance", "settings"],
    "SME Customer": ["portal"],
}


@router.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)):
    s = get_settings()
    return {"status": "ok", "as_of": get_as_of_date().isoformat(), "smes": db.scalar(select(func.count(SME.id))), "llm": {"provider": s.llm_provider if llm.available() else "none", "enabled": llm.available()},
            "demo_step_delay_ms": s.demo_step_delay_ms, "data": "SYNTHETIC"}


@router.get("/roles", tags=["system"])
def roles(db: Session = Depends(get_db)):
    return {"roles": [{"role": r, "user": DEFAULT_USER[r], "pages": NAV[r]} for r in ROLES], "portal_sme_id": (db.get(AppSetting, "portal_sme_id").value if db.get(AppSetting, "portal_sme_id") else "SME-1003"),
            "note": "Demo role switcher - roles are enforced by the API through the X-Demo-Role header. This is not real authentication."}


# ---------------------------------------------------------------------------- overview / SMEs
@router.get("/overview", tags=["overview"], dependencies=[Depends(staff)])
def overview(db: Session = Depends(get_db)):
    return dashboard.overview(db)


@router.get("/smes", tags=["smes"], dependencies=[Depends(staff)])
def smes(q: str | None = None, segment: str | None = None, industry: str | None = None, rm: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    query = select(SME).order_by(SME.id)
    if segment:
        query = query.where(SME.segment == segment)
    if industry:
        query = query.where(SME.industry == industry)
    if rm:
        query = query.where(SME.assigned_rm == rm)
    if status:
        query = query.where(SME.onboarding_status == status)
    if q:
        query = query.where(SME.name.ilike(f"%{q}%") | SME.id.ilike(f"%{q}%"))
    out = []
    for s in db.scalars(query):
        a = repo.latest_assessment(db, s.id)
        al = repo.active_alert(db, s.id)
        out.append({**sme_summary(s), "score": a.model_score if a else None, "risk_category": a.risk_category if a else None, "alert_level": al.priority_level if al else None})
    return {"smes": out, "industries": sorted({r[0] for r in db.execute(select(SME.industry).distinct())}), "rms": sorted({r[0] for r in db.execute(select(SME.assigned_rm).distinct())})}


@router.get("/smes/{sme_id}/360", tags=["smes"])
def sme_360(sme_id: str, reveal: bool = False, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    if ctx.role == "SME Customer":
        raise HTTPException(403, "Customer 360 is a staff view. Use the SME portal.")
    s = db.get(SME, sme_id)
    if not s:
        raise HTTPException(404, "SME not found")
    if reveal and ctx.can_reveal:
        log_audit(db, actor=ctx.user, role=ctx.role, action="pii_revealed", category="system", entity_type="sme", entity_id=sme_id, sme_id=sme_id, summary=f"{ctx.role} revealed PAN/GSTIN for {s.name}", severity="attention")
        db.commit()
    return dashboard.customer_360(db, s, reveal, ctx.role)


# ---------------------------------------------------------------------------- governance
@router.get("/governance/audit", tags=["governance"], dependencies=[Depends(staff)])
def audit(category: str | None = None, sme_id: str | None = None, actor: str | None = None, severity: str | None = None, ai_only: bool = False, q: str | None = None,
          limit: int = Query(100, le=500), offset: int = 0, db: Session = Depends(get_db)):
    query = select(AuditEvent).order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
    if category:
        query = query.where(AuditEvent.category == category)
    if sme_id:
        query = query.where(AuditEvent.sme_id == sme_id)
    if actor:
        query = query.where(AuditEvent.actor.ilike(f"%{actor}%"))
    if severity:
        query = query.where(AuditEvent.severity == severity)
    if ai_only:
        query = query.where(AuditEvent.is_ai_activity == True)  # noqa: E712
    if q:
        query = query.where(AuditEvent.summary.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.limit(limit).offset(offset))
    return {"total": total, "events": [{"id": e.id, "timestamp": e.timestamp.isoformat(timespec="seconds") + "Z", "actor": e.actor, "role": e.role, "action": e.action, "category": e.category,
                                         "entity_type": e.entity_type, "entity_id": e.entity_id, "sme_id": e.sme_id, "run_id": e.run_id, "summary": e.summary, "severity": e.severity,
                                         "details": e.details, "is_ai": e.is_ai_activity} for e in rows],
            "categories": [r[0] for r in db.execute(select(AuditEvent.category).distinct())]}


@router.get("/governance/summary", tags=["governance"], dependencies=[Depends(staff)])
def governance_summary(db: Session = Depends(get_db)):
    reviews = list(db.scalars(select(HumanReviewDecision)))
    decided = [r for r in reviews if r.status == "decided"]
    return {"audit_events": db.scalar(select(func.count(AuditEvent.id))), "ai_events": db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.is_ai_activity == True)),  # noqa: E712
            "reviews": {"pending": len([r for r in reviews if r.status == "pending"]), "decided": len(decided), "by_action": {a: len([r for r in decided if r.action == a]) for a in ("approve", "reject", "request_info")}},
            "overrides": [{"id": r.id, "sme_id": r.sme_id, "kind": r.review_kind, "action": r.action, "reviewer": r.reviewer_name, "role": r.reviewer_role, "reason": r.reason,
                           "decided_at": r.decided_at.isoformat(timespec="seconds") if r.decided_at else None} for r in decided[-8:]],
            "workflow_runs": db.scalar(select(func.count(WorkflowRun.id))), "failed_runs": db.scalar(select(func.count(WorkflowRun.id)).where(WorkflowRun.status == "failed")),
            "controls": [
                {"control": "Human-in-the-loop", "status": "active", "detail": "KYC exceptions, credit referrals and critical escalations pause the graph via interrupt() until an authorised role decides. There is no auto-approval path."},
                {"control": "Sensitive data masking", "status": "active", "detail": "PAN and GSTIN are masked by default; only KYC and Credit Analyst roles can reveal them and each reveal is audited. Aadhaar is never stored beyond the last 4 digits."},
                {"control": "Synthetic data labelling", "status": "active", "detail": "All SME, transaction, bureau and document data is synthetic. Utilization history and external verification are SIMULATED and labelled."},
                {"control": "Model transparency", "status": "active", "detail": "Credit PD is an XGBoost model trained on synthetic labels with SHAP explanations; the policy engine is separate and configurable."},
                {"control": "Consent gating", "status": "active", "detail": "Outreach recommendations are blocked for SMEs whose marketing / data-use consent is not granted."},
            ]}


@router.get("/governance/policy", tags=["governance"], dependencies=[Depends(staff)])
def get_policy():
    return {"policy": policy_svc.get_policy(), "defaults": policy_svc.DEFAULT_POLICY, "rules": [{"rule_id": r, "title": t, "review_kind": k} for r, t, k in policy_svc.RULE_CATALOG],
            "allowed_roles": policy_svc.ALLOWED_ROLES}


@router.put("/governance/policy", tags=["governance"])
def put_policy(body: dict, ctx: Ctx = Depends(require_roles("Risk Manager")), db: Session = Depends(get_db)):
    before = policy_svc.get_policy()
    try:
        after = policy_svc.set_policy(body.get("policy") or {})
    except (ValueError, TypeError) as e:
        raise HTTPException(422, f"Invalid policy value: {e}")
    changed = {k: {"from": before[k], "to": after[k]} for k in after if before.get(k) != after[k]}
    log_audit(db, actor=ctx.user, role=ctx.role, action="policy_updated", category="system", entity_type="policy", entity_id="policy_config", summary=f"{ctx.user} changed {len(changed)} policy threshold(s)",
              severity="attention", details=changed)
    db.commit()
    return {"policy": after, "changed": changed}


@router.get("/models/performance", tags=["models"])
def models_performance(refresh: bool = False, ctx: Ctx = Depends(get_ctx)):
    if ctx.role == "SME Customer":
        raise HTTPException(403, "Not available to the SME Customer role.")
    return evaluation.get_cached(refresh)


# ---------------------------------------------------------------------------- settings / reset
@router.get("/settings", tags=["settings"], dependencies=[Depends(staff)])
def settings(db: Session = Depends(get_db)):
    s = get_settings()
    info = db.get(AppSetting, "seed_info")
    return {"demo_step_delay_ms": s.demo_step_delay_ms, "as_of_date": get_as_of_date().isoformat(), "llm": {"provider": s.llm_provider, "enabled": llm.available(), "note": "Configured via environment variables; keys are never shown or stored."},
            "credit_model_available": bool(db.get(AppSetting, "credit_model_available").value) if db.get(AppSetting, "credit_model_available") else True,
            "seed": info.value if info else None, "fault_injection": "Available per run in AI Workflow Studio (testing option, clearly labelled).", "data": "SYNTHETIC", "version": "0.1.0"}


@router.put("/settings", tags=["settings"])
def put_settings(body: dict, ctx: Ctx = Depends(require_roles("Risk Manager", "Relationship Manager", "Credit Analyst", "KYC Analyst")), db: Session = Depends(get_db)):
    s = get_settings()
    if "demo_step_delay_ms" in body:
        s.demo_step_delay_ms = max(0, min(3000, int(body["demo_step_delay_ms"])))
        log_audit(db, actor=ctx.user, role=ctx.role, action="setting_changed", category="system", entity_type="setting", entity_id="demo_step_delay_ms", summary=f"Workflow demo step delay set to {s.demo_step_delay_ms} ms")
        db.commit()
    return {"demo_step_delay_ms": s.demo_step_delay_ms}


@router.post("/admin/reset", tags=["settings"])
def reset(body: dict | None = None, ctx: Ctx = Depends(require_roles("Risk Manager", "Relationship Manager", "Credit Analyst", "KYC Analyst"))):
    res = seed_mod.reset_all((body or {}).get("with_workflows"))
    from app.db.session import session_scope

    with session_scope() as s:
        log_audit(s, actor=ctx.user, role=ctx.role, action="demo_reset", category="system", entity_type="system", entity_id="reset", summary=f"Demo data reset and reseeded by {ctx.user}", severity="attention")
    return res


@router.get("/notifications", tags=["system"], dependencies=[Depends(staff)])
def notifications(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    items = []
    for r in db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.status == "pending").order_by(HumanReviewDecision.created_at.desc())):
        s = db.get(SME, r.sme_id)
        items.append({"id": r.id, "type": "review", "title": f"Review needed: {r.review_kind.replace('_', ' ')}", "detail": s.name, "sme_id": r.sme_id, "for_you": ctx.role in (r.allowed_roles or []), "created_at": r.created_at.isoformat(timespec="seconds")})
    for a in db.scalars(select(Alert).where(Alert.review_status == "open", Alert.priority_level == "critical").order_by(Alert.priority_score.desc()).limit(5)):
        s = db.get(SME, a.sme_id)
        items.append({"id": a.id, "type": "alert", "title": f"Critical alert: {a.primary_signal.replace('_', ' ')}", "detail": s.name, "sme_id": a.sme_id, "for_you": ctx.role in ("Relationship Manager", "Risk Manager"), "created_at": a.created_at.isoformat(timespec="seconds")})
    return {"items": items, "count": len([i for i in items if i["for_you"]])}
