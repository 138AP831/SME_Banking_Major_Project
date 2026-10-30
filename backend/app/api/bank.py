"""Bank Portal endpoints added by the two-sided architecture update (bank-restricted: staff roles only).

* New loan applications: intake bundle (step 1) and explainable decision support (step 3). Credit assessment (step 2) is the existing
  credit module / ``credit_assessment`` graph.
* Existing loan monitoring: alerts + prioritisation (steps 4-5) are the existing EWS; this router adds RM contact & verify (step 6) and
  escalation to Credit Risk / Collections (step 7), each with an audit trail.
* Consent & exchange gate visibility for bank users.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Ctx, get_ctx, get_db, require_roles, staff
from app.api.modules import _alert_view
from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.ml.features import build_features
from app.models.entities import SME, Alert, AuditEvent, CreditApplication, Escalation, RMContact, RMFeedback, new_id, utcnow
from app.repositories import data as repo
from app.services import exchange
from app.services.audit import log_audit
from app.services.kyc_service import kyc_summary_for_policy

router = APIRouter(prefix="/api/bank", tags=["bank-portal"], dependencies=[Depends(staff)])
CASE_ROLES = ("Relationship Manager", "Risk Manager", "Credit Analyst")
CHANNELS = ("phone", "visit", "email", "video")
VERIFIED = ("confirmed", "partially_confirmed", "not_confirmed", "unreachable")
OUTCOMES = ("resolved", "improving", "no_change", "worsening")
TARGETS = ("Credit Risk", "Collections")
DECISION_LABEL = {"eligible": ("approve_for_consideration", "Approve for further consideration"), "refer": ("refer_for_human_review", "Refer for human review"),
                  "not_eligible": ("decline_illustrative", "Decline under illustrative demo policy")}
DECISION_NOTE = "Illustrative recommendation from the demo policy engine on synthetic data. No loan has been approved, declined or disbursed; a human decides."


# ---------------------------------------------------------------------------- new loan applications
@router.get("/intake/{sme_id}")
def intake(sme_id: str, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Step 1 - Application intake: everything the bank needs in one organised bundle. Customer-submitted data comes through the consent gate."""
    raw = repo.get_sme_dict(db, sme_id)
    if not raw:
        raise HTTPException(404, "SME not found")
    as_of = get_as_of_date()
    f = build_features(raw, repo.load_txns(db, sme_id, as_of), as_of)
    gated = exchange.bank_view_of_submission(db, sme_id, ctx.user, ctx.role)
    app = repo.open_credit_application(db, sme_id)
    db.commit()
    return to_jsonable({
        "sme_id": sme_id, "as_of": as_of.isoformat(),
        "business_profile": {k: raw[k] for k in ("name", "industry", "legal_form", "city", "state", "incorporation_date", "employees", "declared_annual_turnover", "assigned_rm")},
        "financial_statements": {k: f[k] for k in ("avg_monthly_inflow", "avg_monthly_outflow", "observed_annual_turnover", "declared_to_observed_turnover", "net_cash_margin", "dscr", "inflow_trend_pct")},
        "bank_data": {k: f[k] for k in ("current_balance", "avg_balance_30d", "min_balance_90d", "liquidity_cover_days", "utilization", "sanctioned_limit")} | {"bureau_score": raw["bureau_score"], "bureau_is_synthetic": True},
        "existing_liabilities": {"outstanding_exposure": raw["outstanding_exposure"], "monthly_debt_service": f["monthly_debt_service"], "loan_emi_monthly": raw["loan_emi_monthly"]},
        "repayment_history": {k: f[k] for k in ("repayment_delay_count_6m", "max_repayment_delay", "repayment_delays_recent", "bounce_count_90d")},
        "kyc": to_jsonable(kyc_summary_for_policy(db, sme_id)),
        "application": {"id": app.id, "requested_amount": app.requested_amount, "tenure_months": app.tenure_months, "purpose": app.purpose, "status": app.status} if app else None,
        "customer_submission": {k: gated[k] for k in ("authorised", "reason", "shared_fields", "submission")},
        "note": "Customer-submitted information appears only while the customer's consent for 'loan_application' is granted."})


@router.get("/decision-support/{sme_id}")
def decision_support(sme_id: str, db: Session = Depends(get_db)):
    """Step 3 - Decision support: explainable, illustrative preliminary outcome with its evidence."""
    a = repo.latest_assessment(db, sme_id)
    if not a:
        raise HTTPException(404, "No credit assessment exists for this SME yet. Run the credit assessment workflow first.")
    code, label = DECISION_LABEL.get(a.policy_outcome, (a.policy_outcome, a.policy_outcome))
    human = {"not_required": "No human review required", "pending": "Awaiting human review", "approved_to_proceed": "Human reviewer recommended to proceed",
             "declined_by_reviewer": "Human reviewer declined", "info_requested": "Human reviewer requested more information"}.get(a.human_review_status, a.human_review_status)
    return to_jsonable({"sme_id": sme_id, "assessment_id": a.id, "outcome": code, "outcome_label": label + " (illustrative)", "human_review": {"status": a.human_review_status, "label": human},
                        "why": {"explanation": a.explanation, "policy_rules": a.policy_rules, "positive_factors": a.positive_factors, "risk_factors": a.risk_factors},
                        "evidence": {"model_score": a.model_score, "risk_category": a.risk_category, "requested_amount": a.requested_amount, "suggested_limit": [a.limit_low, a.limit_high]},
                        "is_illustrative": True, "loan_approved": False, "disclaimer": DECISION_NOTE, "created_at": a.created_at.isoformat(timespec="seconds")})


# ---------------------------------------------------------------------------- existing loan monitoring: case work
def _contact_view(c: RMContact, as_of: date) -> dict:
    return {"id": c.id, "rm_name": c.rm_name, "channel": c.channel, "issue_verified": c.issue_verified, "business_situation": c.business_situation, "outcome": c.outcome, "notes": c.notes,
            "commitment": ({"amount": c.commitment_amount, "due": c.commitment_due.isoformat() if c.commitment_due else None, "status": c.commitment_status,
                            "overdue": bool(c.commitment_status == "pending" and c.commitment_due and c.commitment_due < as_of)} if c.commitment_status != "none" else None),
            "created_at": c.created_at.isoformat(timespec="seconds")}


def _esc_view(e: Escalation) -> dict:
    return {"id": e.id, "target": e.target, "trigger": e.trigger, "reason": e.reason, "status": e.status, "resolution": e.resolution, "evidence": e.evidence, "created_by": e.created_by,
            "created_role": e.created_role, "created_at": e.created_at.isoformat(timespec="seconds"), "updated_at": e.updated_at.isoformat(timespec="seconds"), "alert_id": e.alert_id, "sme_id": e.sme_id}


def _eligibility(alert: Alert, contacts: list[RMContact], escs: list[Escalation], as_of: date) -> dict:
    if any(e.status in ("open", "accepted") for e in escs):
        return {"can_escalate": False, "reason": "This case is already escalated.", "trigger": None}
    if alert.review_status in ("resolved", "dismissed"):
        return {"can_escalate": False, "reason": "The alert is resolved or dismissed.", "trigger": None}
    if not contacts:
        return {"can_escalate": False, "reason": "Contact and verify with the borrower first, then escalate if it is unresolved.", "trigger": None}
    missed = any(c.commitment_status == "missed" or (c.commitment_status == "pending" and c.commitment_due and c.commitment_due < as_of) for c in contacts)
    persists = contacts[-1].outcome in ("no_change", "worsening")
    if missed and persists:
        return {"can_escalate": True, "reason": None, "trigger": "both"}
    if missed:
        return {"can_escalate": True, "reason": None, "trigger": "commitment_missed"}
    if persists:
        return {"can_escalate": True, "reason": None, "trigger": "distress_persists"}
    return {"can_escalate": False, "reason": "The latest contact shows the situation is improving or resolved and no commitment has been missed.", "trigger": None}


def _case(db: Session, alert: Alert) -> dict:
    as_of = get_as_of_date()
    contacts = list(db.scalars(select(RMContact).where(RMContact.alert_id == alert.id).order_by(RMContact.created_at)))
    escs = list(db.scalars(select(Escalation).where(Escalation.alert_id == alert.id).order_by(Escalation.created_at)))
    sme = db.get(SME, alert.sme_id)
    events = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.sme_id == alert.sme_id, AuditEvent.category.in_(["monitoring", "alert"])).order_by(AuditEvent.timestamp.desc()).limit(80))
              if (e.details or {}).get("alert_id") == alert.id or e.entity_id == alert.id]
    return {"alert": _alert_view(alert, sme), "contacts": [_contact_view(c, as_of) for c in contacts], "escalations": [_esc_view(e) for e in escs], "eligibility": _eligibility(alert, contacts, escs, as_of),
            "timeline": [{"at": e.timestamp.isoformat(timespec="seconds") + "Z", "actor": e.actor, "role": e.role, "action": e.action, "summary": e.summary} for e in events],
            "steps": {"early_warning": True, "prioritised": True, "contact_verify": bool(contacts), "escalated": bool(escs)}}


@router.get("/cases/{alert_id}")
def case(alert_id: str, db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    return _case(db, a)


@router.post("/cases/{alert_id}/contacts")
def record_contact(alert_id: str, body: dict, ctx: Ctx = Depends(require_roles(*CASE_ROLES)), db: Session = Depends(get_db)):
    """Step 6 - RM contact & verify: record the conversation, whether the signal was accurate, the situation, any repayment commitment and the outcome."""
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    if a.review_status in ("resolved", "dismissed"):
        raise HTTPException(409, "This alert is already closed.")
    channel, verified, outcome = body.get("channel"), body.get("issue_verified"), body.get("outcome")
    if channel not in CHANNELS or verified not in VERIFIED or outcome not in OUTCOMES:
        raise HTTPException(400, f"channel must be one of {CHANNELS}; issue_verified one of {VERIFIED}; outcome one of {OUTCOMES}.")
    situation = (body.get("business_situation") or "").strip()
    if len(situation) < 10:
        raise HTTPException(400, "Describe the business situation (at least 10 characters).")
    amt, due = body.get("commitment_amount"), body.get("commitment_due")
    c_amount = c_due = None
    status = "none"
    if amt not in (None, "") or due:
        try:
            c_amount, c_due = float(amt), date.fromisoformat(str(due)[:10])
        except (TypeError, ValueError):
            raise HTTPException(400, "A repayment commitment needs an amount and a due date (YYYY-MM-DD).")
        if c_amount <= 0:
            raise HTTPException(400, "The commitment amount must be positive.")
        status = "pending"
    c = RMContact(id=new_id("CTC"), alert_id=a.id, sme_id=a.sme_id, rm_name=ctx.user, rm_role=ctx.role, channel=channel, issue_verified=verified, business_situation=situation, outcome=outcome,
                  commitment_amount=c_amount, commitment_due=c_due, commitment_status=status, notes=(body.get("notes") or "").strip()[:500])
    db.add(c)
    # reuse the existing RM-feedback loop so prioritisation keeps learning from contact outcomes
    db.add(RMFeedback(id=new_id("FBK"), alert_id=a.id, sme_id=a.sme_id, rm_name=ctx.user, action_taken=f"RM contact ({channel}); signal {verified.replace('_', ' ')}", outcome=outcome, notes=situation[:300]))
    if a.review_status != "escalated":
        a.review_status = "resolved" if outcome == "resolved" else "in_review"
    a.updated_at = utcnow()
    name = db.get(SME, a.sme_id).name
    log_audit(db, actor=ctx.user, role=ctx.role, action="rm_contact_recorded", category="monitoring", entity_type="rm_contact", entity_id=c.id, sme_id=a.sme_id,
              summary=f"{ctx.user} contacted {name} by {channel}: signal {verified.replace('_', ' ')}, outcome {outcome}" + (f", commitment {c_amount:,.0f} due {c_due}" if status == "pending" else ""),
              details={"alert_id": a.id, "verified": verified, "outcome": outcome, "commitment": status})
    db.commit()
    return _case(db, a)


@router.post("/contacts/{contact_id}/commitment")
def commitment_status(contact_id: str, body: dict, ctx: Ctx = Depends(require_roles(*CASE_ROLES)), db: Session = Depends(get_db)):
    c = db.get(RMContact, contact_id)
    if not c or c.commitment_status == "none":
        raise HTTPException(404, "Commitment not found")
    new = body.get("status")
    if new not in ("kept", "missed"):
        raise HTTPException(400, "status must be 'kept' or 'missed'.")
    c.commitment_status = new
    a = db.get(Alert, c.alert_id)
    log_audit(db, actor=ctx.user, role=ctx.role, action=f"commitment_{new}", category="monitoring", entity_type="rm_contact", entity_id=c.id, sme_id=c.sme_id,
              summary=f"Repayment commitment of {c.commitment_amount:,.0f} due {c.commitment_due} recorded as {new}", severity="attention" if new == "missed" else "info", details={"alert_id": c.alert_id})
    db.commit()
    return _case(db, a)


@router.post("/cases/{alert_id}/escalate")
def escalate(alert_id: str, body: dict, ctx: Ctx = Depends(require_roles(*CASE_ROLES)), db: Session = Depends(get_db)):
    """Step 7 - Escalate if unresolved: refer to Credit Risk or Collections with the reason and a frozen copy of the supporting evidence."""
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    target, reason = body.get("target"), (body.get("reason") or "").strip()
    if target not in TARGETS:
        raise HTTPException(400, f"target must be one of {TARGETS}.")
    if len(reason) < 10:
        raise HTTPException(400, "A reason of at least 10 characters is required for the audit trail.")
    as_of = get_as_of_date()
    contacts = list(db.scalars(select(RMContact).where(RMContact.alert_id == a.id).order_by(RMContact.created_at)))
    escs = list(db.scalars(select(Escalation).where(Escalation.alert_id == a.id)))
    el = _eligibility(a, contacts, escs, as_of)
    if not el["can_escalate"]:
        raise HTTPException(409, el["reason"])
    fc = repo.latest_forecast(db, a.sme_id)
    sme = db.get(SME, a.sme_id)
    evidence = to_jsonable({"alert": {"priority_score": a.priority_score, "priority_level": a.priority_level, "components": a.components, "primary_signal": a.primary_signal,
                                       "signals": [{"label": s.get("label"), "description": s.get("description"), "severity": s.get("severity")} for s in (a.signals or [])], "days_to_stress": a.days_to_stress},
                            "exposure": a.exposure, "contacts": [_contact_view(c, as_of) for c in contacts], "forecast": (fc.result.get("summary") if fc else None), "captured_at": utcnow().isoformat(timespec="seconds")})
    e = Escalation(id=new_id("ESC"), alert_id=a.id, sme_id=a.sme_id, target=target, trigger=el["trigger"], reason=reason, evidence=evidence, created_by=ctx.user, created_role=ctx.role)
    db.add(e)
    a.review_status = "escalated"
    a.updated_at = utcnow()
    db.flush()
    log_audit(db, actor=ctx.user, role=ctx.role, action="case_escalated", category="monitoring", entity_type="escalation", entity_id=e.id, sme_id=a.sme_id, severity="attention",
              summary=f"{sme.name} escalated to {target} ({el['trigger'].replace('_', ' ')}): {reason[:140]}", details={"alert_id": a.id, "target": target, "trigger": el["trigger"], "exposure": a.exposure})
    db.commit()
    return _case(db, a)


@router.get("/escalations")
def escalations(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Escalation, SME.name).join(SME, SME.id == Escalation.sme_id).order_by(Escalation.created_at.desc())
    if status:
        q = q.where(Escalation.status == status)
    return [{**_esc_view(e), "sme_name": n} for e, n in db.execute(q)]


@router.post("/escalations/{esc_id}/status")
def escalation_status(esc_id: str, body: dict, ctx: Ctx = Depends(require_roles("Risk Manager", "Credit Analyst")), db: Session = Depends(get_db)):
    e = db.get(Escalation, esc_id)
    if not e:
        raise HTTPException(404, "Escalation not found")
    new, res = body.get("status"), (body.get("resolution") or "").strip()
    if new not in ("accepted", "closed"):
        raise HTTPException(400, "status must be 'accepted' or 'closed'.")
    if new == "closed" and len(res) < 10:
        raise HTTPException(400, "Record how the escalation was resolved (at least 10 characters).")
    if e.status == "closed":
        raise HTTPException(409, "This escalation is already closed.")
    e.status, e.resolution, e.updated_at = new, (res or e.resolution), utcnow()
    log_audit(db, actor=ctx.user, role=ctx.role, action=f"escalation_{new}", category="monitoring", entity_type="escalation", entity_id=e.id, sme_id=e.sme_id,
              summary=f"{e.target} {new} the escalation" + (f": {res[:140]}" if res else ""), details={"alert_id": e.alert_id})
    db.commit()
    return _esc_view(e)


# ---------------------------------------------------------------------------- consent & exchange gate (bank view)
@router.get("/exchange/{sme_id}")
def exchange_status(sme_id: str, db: Session = Depends(get_db)):
    if not db.get(SME, sme_id):
        raise HTTPException(404, "SME not found")
    return {"sme_id": sme_id, "consent": exchange.consent_state(db, sme_id), "history": exchange.consent_history(db, sme_id),
            "purposes": {k: {"label": v["label"], "direction": v["direction"], "shares": v["shares"]} for k, v in exchange.PURPOSES.items()},
            "note": "The bank sees the customer's submission only while 'loan_application' consent is granted. Every read through the gate is audited."}


@router.get("/architecture")
def architecture():
    """Machine-readable description of the two-sided architecture, so the app and the diagram agree."""
    return {"portals": {"bank": {"workflows": [
        {"name": "New loan applications", "steps": ["Application intake", "Credit risk assessment", "Decision support"], "graph": "credit_assessment"},
        {"name": "Existing loan monitoring", "steps": ["Early warning", "Risk prioritization", "RM contact & verify", "Escalate if unresolved"], "graph": "cashflow_ews"}]},
        "customer": {"workflows": [
            {"name": "Financial readiness", "steps": ["Upload business information", "Financial profile analysis", "Indicative loan readiness"], "graph": "customer_readiness"},
            {"name": "Improvement & reassessment", "steps": ["Personalized improvement plan", "Business growth intelligence", "Track progress & reassess"], "graph": "customer_improvement"}]}},
        "data_layer": [{"id": "bank_restricted", "label": "Bank-restricted data", "contents": ["Loan applications", "Loan accounts", "Repayment history", "Early-warning alerts", "Confidential bank risk notes"]},
                       {"id": "exchange_gate", "label": "Consent & exchange gate", "contents": ["Explicit consent status", "Purpose-specific sharing", "Role-based access", "Consent history and audit logs"]},
                       {"id": "customer_domain", "label": "Customer data domain", "contents": ["Business profile", "Uploaded statements", "KYC documents", "Requested loan details", "Action-plan progress"]},
                       {"id": "external_market", "label": "External market data", "contents": ["Google Places listings (or labelled synthetic)", "Location signals", "Competitor context"]}],
        "controls": ["Consent & privacy", "Separate role-based access", "Explainable outputs", "Human review", "Audit trail"],
        "purposes": list(exchange.PURPOSES)}
