"""Customer Data Domain services: submissions, financial profile, readiness history, improvement plan, market snapshots.

Everything returned from here is customer-safe: no bank scores, exposure, alerts, RM notes or policy details.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.util import inr, to_jsonable
from app.ml.features import build_features
from app.models.entities import (SME, AuditEvent, CustomerSubmission, ImprovementAction, MarketSnapshot, ReadinessAssessment, new_id, utcnow)
from app.repositories import data as repo
from app.services import analysis, exchange, kyc_service, readiness
from app.services.audit import log_audit

PROFILE_KEYS = ("business_name", "industry", "employees", "legal_form", "description")
FIN_KEYS = ("annual_revenue", "annual_expenses", "other_liabilities", "monthly_receivables")


def _num(v: Any, label: str, lo: float = 0, hi: float = 1e12, required: bool = False) -> float | None:
    if v in (None, ""):
        if required:
            raise ValueError(f"{label} is required.")
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if x < lo or x > hi:
        raise ValueError(f"{label} must be between {lo:,.0f} and {hi:,.0f}.")
    return x


def actor_for(sme: SME) -> str:
    return f"{sme.name} (portal)"


# --------------------------------------------------------------------------- Step 1: submission
def submit(db: Session, sme: SME, body: dict[str, Any]) -> CustomerSubmission:
    prev = exchange.latest_submission(db, sme.id)
    profile = {k: (str(body.get("business_profile", {}).get(k, ""))[:240]) for k in PROFILE_KEYS if body.get("business_profile", {}).get(k) not in (None, "")}
    fin = {k: _num(body.get("financials", {}).get(k), k.replace("_", " ").capitalize()) for k in FIN_KEYS}
    fin = {k: v for k, v in fin.items() if v is not None}
    if "annual_revenue" not in fin:
        raise ValueError("Annual revenue is required.")
    loans = []
    for l in (body.get("existing_loans") or [])[:10]:
        if not (l.get("lender") or "").strip():
            continue
        loans.append({"lender": str(l["lender"])[:80], "outstanding": _num(l.get("outstanding"), "Outstanding amount") or 0.0, "monthly_emi": _num(l.get("monthly_emi"), "Monthly instalment") or 0.0})
    amount = _num(body.get("requested_amount"), "Requested amount", 100_000, 100_000_000)
    loc = {k: str(body.get("location", {}).get(k, ""))[:60] for k in ("city", "state", "pincode") if body.get("location", {}).get(k)}
    if not loc.get("city"):
        loc["city"] = sme.city
    row = CustomerSubmission(id=new_id("SUB"), sme_id=sme.id, version=(prev.version + 1) if prev else 1, business_profile=profile, financials=fin, existing_loans=loans, requested_amount=amount,
                             tenure_months=int(_num(body.get("tenure_months"), "Tenure", 3, 120) or 24), purpose=str(body.get("purpose") or "")[:120], location=loc, submitted_by=actor_for(sme))
    db.add(row)
    db.flush()
    log_audit(db, actor=actor_for(sme), role="SME Customer", action="customer_submission_saved", category="customer", entity_type="customer_submission", entity_id=row.id, sme_id=sme.id,
              summary=f"{sme.name} saved business information (version {row.version})", details={"version": row.version, "fields": sorted([*profile, *fin])})
    return row


def submission_view(s: CustomerSubmission | None, as_of: date | None = None) -> dict[str, Any] | None:
    if not s:
        return None
    d = exchange.submission_dict(s)
    d["age_days"] = ((as_of or date.today()) - s.created_at.date()).days if as_of else (datetime.utcnow() - s.created_at).days
    return d


def docs_summary(db: Session, sme_id: str) -> dict[str, Any]:
    kyc = repo.latest_kyc(db, sme_id)
    if not kyc:
        return {"required_count": 0, "present_count": 0, "documents": [], "missing": []}
    view = kyc_service.application_view(db, kyc, "SME Customer", False)
    reqs = view["required_documents"]
    return {"required_count": len(reqs), "present_count": sum(1 for d in reqs if d["present"]), "documents": [{"label": d["label"], "uploaded": d["present"]} for d in reqs],
            "missing": [d["label"] for d in reqs if not d["present"]]}


# --------------------------------------------------------------------------- Step 2: financial profile (same inputs as the graph nodes)
def build_profile(features: dict[str, Any], forecast: dict[str, Any] | None, submission: dict[str, Any] | None) -> dict[str, Any]:
    f = features
    fin = (submission or {}).get("financials", {})
    declared_emi = sum(float(l.get("monthly_emi") or 0) for l in (submission or {}).get("existing_loans", []))
    sm = (forecast or {}).get("summary") or {}
    return {
        "revenue": {"avg_monthly_inflow": f["avg_monthly_inflow"], "annualised_from_account": f["observed_annual_turnover"], "declared_annual_revenue": fin.get("annual_revenue"), "trend_pct_per_month": f["inflow_trend_pct"],
                    "last_30d_vs_prior_pct": f.get("inflow_last30_vs_prev60_pct")},
        "expenses": {"avg_monthly_outflow": f["avg_monthly_outflow"], "declared_annual_expenses": fin.get("annual_expenses")},
        "liabilities": {"monthly_debt_service_seen": f["monthly_debt_service"], "declared_monthly_emi": declared_emi or None, "declared_other_liabilities": fin.get("other_liabilities")},
        "repayment": {"late_instalments_6m": f["repayment_delay_count_6m"], "returned_payments_90d": f["bounce_count_90d"], "max_delay_days": f["max_repayment_delay"]},
        "cash_flow": {"monthly_inflows": list(reversed(f["monthly_inflows"])), "monthly_outflows": list(reversed(f["monthly_outflows"])), "net_cash_margin": f["net_cash_margin"],
                      "days_to_shortfall": sm.get("days_to_shortfall"), "shortfall_date": sm.get("shortfall_date"), "min_projected_balance": sm.get("min_balance")},
        "health": {"liquidity_cover_days": min(f["liquidity_cover_days"], 365), "current_balance": f["current_balance"], "top_customer_share": f["top_customer_share"], "history_days": f["data_days"]},
    }


def live_inputs(db: Session, sme_id: str, as_of: date) -> dict[str, Any]:
    sme = exchange.customer_sme_view(repo.get_sme_dict(db, sme_id))
    sme["incorporation_date"] = date.fromisoformat(str(sme["incorporation_date"])[:10]) if sme.get("incorporation_date") else None
    txns = repo.load_txns(db, sme_id, as_of)
    feats = build_features(sme, txns, as_of)
    fc = analysis.compute_forecast(sme, txns, as_of, 60, None)
    sub = submission_view(exchange.latest_submission(db, sme_id), as_of)
    return {"sme": sme, "features": feats, "forecast": fc, "submission": sub, "docs": docs_summary(db, sme_id)}


# --------------------------------------------------------------------------- readiness persistence / views
def latest_readiness(db: Session, sme_id: str) -> ReadinessAssessment | None:
    return db.scalars(select(ReadinessAssessment).where(ReadinessAssessment.sme_id == sme_id).order_by(ReadinessAssessment.version.desc())).first()


def readiness_view(row: ReadinessAssessment | None, prev: ReadinessAssessment | None = None) -> dict[str, Any] | None:
    if not row:
        return None
    d = {"id": row.id, "version": row.version, "status": row.status, "status_label": readiness.STATUS_LABEL.get(row.status, row.status), "readiness_index": row.readiness_index, "indicators": row.indicators,
         "strengths": row.strengths, "gaps": row.gaps, "explanation": row.explanation, "is_reassessment": row.is_reassessment, "created_at": row.created_at.isoformat(timespec="seconds"),
         "criteria_version": readiness.CRITERIA_VERSION, "disclaimer": readiness.DISCLAIMER, "submission_version": (row.inputs or {}).get("submission_version")}
    d["change"] = {"previous_index": prev.readiness_index, "delta": row.readiness_index - prev.readiness_index, "previous_status_label": readiness.STATUS_LABEL.get(prev.status, prev.status)} if prev else None
    return d


def readiness_history(db: Session, sme_id: str) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(ReadinessAssessment).where(ReadinessAssessment.sme_id == sme_id).order_by(ReadinessAssessment.version)))
    return [{"version": r.version, "created_at": r.created_at.isoformat(timespec="seconds"), "status_label": readiness.STATUS_LABEL.get(r.status, r.status), "readiness_index": r.readiness_index,
             "is_reassessment": r.is_reassessment, "submission_version": (r.inputs or {}).get("submission_version")} for r in rows]


def persist_readiness(db: Session, sme: dict[str, Any], run_id: str | None, r: dict[str, Any], text: str, submission: dict[str, Any] | None, reassessment: bool, actor: str = "SMEFlow AI", role: str = "system") -> dict[str, Any]:
    prev = latest_readiness(db, sme["id"])
    row = ReadinessAssessment(id=new_id("RDY"), sme_id=sme["id"], run_id=run_id, submission_id=(submission or {}).get("id"), version=(prev.version + 1) if prev else 1, status=r["status"],
                              readiness_index=r["readiness_index"], indicators=to_jsonable(r["indicators"]), strengths=to_jsonable(r["strengths"]), gaps=to_jsonable(r["gaps"]), explanation=text,
                              is_reassessment=reassessment, inputs=to_jsonable({"submission_version": (submission or {}).get("version"), "as_of": r["as_of"], "criteria": r["criteria_version"], "profile": r.get("profile")}))
    db.add(row)
    db.flush()
    delta = row.readiness_index - prev.readiness_index if prev else None
    log_audit(db, actor=actor, role=role, action="reassessment_completed" if reassessment else "readiness_assessed", category="customer", entity_type="readiness_assessment", entity_id=row.id, sme_id=sme["id"], run_id=run_id,
              summary=f"Indicative loan-readiness {'reassessment' if reassessment else 'assessment'} v{row.version} for {sme['name']}: {readiness.STATUS_LABEL[row.status]}" + (f" ({delta:+d} vs previous)" if delta is not None else ""),
              details={"version": row.version, "status": row.status, "index": row.readiness_index, "delta": delta, "submission_version": (submission or {}).get("version")}, is_ai=True)
    return {"assessment_id": row.id, "version": row.version, "delta": delta}


# --------------------------------------------------------------------------- improvement plan
def sync_improvement_plan(db: Session, sme_id: str, assessment: ReadinessAssessment, items: list[dict[str, Any]]) -> dict[str, int]:
    existing = {a.code: a for a in db.scalars(select(ImprovementAction).where(ImprovementAction.sme_id == sme_id, ImprovementAction.status.in_(["open", "done"])))}
    wanted = {i["code"] for i in items}
    created = resolved = 0
    for it in items:
        a = existing.get(it["code"])
        if a:
            a.why, a.priority, a.assessment_id = it["why"], it["priority"], assessment.id  # progress the customer recorded is kept
            continue
        db.add(ImprovementAction(id=new_id("ACT"), sme_id=sme_id, assessment_id=assessment.id, code=it["code"], title=it["title"], why=it["why"], how=it["how"], priority=it["priority"]))
        created += 1
    for code, a in existing.items():
        if code not in wanted and a.status == "open":
            a.status, a.completed_at = "resolved", utcnow()  # the indicator is now good in the latest assessment
            resolved += 1
    db.flush()
    return {"created": created, "resolved_by_reassessment": resolved}


def plan_view(db: Session, sme_id: str) -> dict[str, Any]:
    acts = list(db.scalars(select(ImprovementAction).where(ImprovementAction.sme_id == sme_id).order_by(ImprovementAction.priority, ImprovementAction.created_at)))
    cur = latest_readiness(db, sme_id)
    ind = {i["code"]: i["status"] for i in (cur.indicators if cur else [])}
    items = [{"id": a.id, "code": a.code, "title": a.title, "why": a.why, "how": a.how, "status": a.status, "completed_at": a.completed_at.isoformat(timespec="seconds") if a.completed_at else None,
              "latest_indicator_status": ind.get(a.code)} for a in acts]
    done = sum(1 for a in acts if a.status in ("done", "resolved"))
    return {"items": items, "progress": {"total": len(items), "completed": done, "open": len(items) - done, "percent": round(done / len(items) * 100) if items else 0}}


def set_action_status(db: Session, sme: SME, action_id: str, done: bool) -> ImprovementAction:
    a = db.get(ImprovementAction, action_id)
    if not a or a.sme_id != sme.id:
        raise LookupError("Action not found")
    if a.status == "resolved":
        raise ValueError("This action was already resolved by your latest assessment.")
    a.status, a.completed_at = ("done", utcnow()) if done else ("open", None)
    log_audit(db, actor=actor_for(sme), role="SME Customer", action="improvement_action_completed" if done else "improvement_action_reopened", category="customer", entity_type="improvement_action", entity_id=a.id, sme_id=sme.id,
              summary=f"{sme.name} marked '{a.title}' as {'done' if done else 'not done'}", details={"code": a.code})
    return a


# --------------------------------------------------------------------------- market
def save_market(db: Session, sme_id: str, run_id: str | None, market: dict[str, Any], growth: dict[str, Any] | None) -> str:
    row = MarketSnapshot(id=new_id("MKT"), sme_id=sme_id, run_id=run_id, source=market.get("source", "synthetic_demo"), payload=to_jsonable({"market": market, "growth": growth}))
    db.add(row)
    db.flush()
    return row.id


def latest_market(db: Session, sme_id: str) -> dict[str, Any] | None:
    row = db.scalars(select(MarketSnapshot).where(MarketSnapshot.sme_id == sme_id).order_by(MarketSnapshot.created_at.desc())).first()
    return {**row.payload, "created_at": row.created_at.isoformat(timespec="seconds")} if row else None


# --------------------------------------------------------------------------- customer-visible activity
def activity(db: Session, sme_id: str, limit: int = 40) -> list[dict[str, Any]]:
    q = select(AuditEvent).where(AuditEvent.sme_id == sme_id, AuditEvent.category.in_(["consent", "customer"])).order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(limit)
    return [{"id": e.id, "at": e.timestamp.isoformat(timespec="seconds") + "Z", "action": e.action, "category": e.category, "summary": e.summary} for e in db.scalars(q)]


__all__ = ["inr"]
