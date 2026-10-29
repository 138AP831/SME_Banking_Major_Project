"""Overview + Customer 360 aggregations. Everything is derived from stored data; nothing is hard-coded."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.models.entities import (SME, Alert, AuditEvent, CashflowForecast, CreditAssessment, HumanReviewDecision, KYCApplication, ProductRecommendation, Transaction, WorkflowRun, utcnow)
from app.repositories import data as repo
from app.services import kyc_service
from app.services.views import sme_summary

RISK_ORDER = ["Low", "Moderate", "Elevated", "High"]


def _latest_per_sme(db: Session, model, ts_col):
    sub = select(model.sme_id, func.max(ts_col).label("m")).group_by(model.sme_id).subquery()
    return db.scalars(select(model).join(sub, (model.sme_id == sub.c.sme_id) & (ts_col == sub.c.m)))


def overview(db: Session) -> dict[str, Any]:
    smes = list(db.scalars(select(SME)))
    clients = [s for s in smes if s.segment == "client" and s.onboarding_status == "active"]
    onboarding = [s for s in smes if s.onboarding_status == "onboarding"]
    prospects = [s for s in smes if s.segment == "prospect"]
    assessments = {a.sme_id: a for a in _latest_per_sme(db, CreditAssessment, CreditAssessment.created_at)}
    forecasts = {f.sme_id: f for f in _latest_per_sme(db, CashflowForecast, CashflowForecast.created_at) if f.is_baseline}
    recs = {r.sme_id: r for r in _latest_per_sme(db, ProductRecommendation, ProductRecommendation.created_at)}
    alerts = list(db.scalars(select(Alert).where(Alert.review_status.in_(["open", "in_review", "escalated"])).order_by(Alert.priority_score.desc())))
    kyc = Counter(a.status for a in db.scalars(select(KYCApplication)) if a.sme_id in {s.id for s in onboarding})
    pending = list(db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.status == "pending")))
    exposure = sum(s.outstanding_exposure for s in clients)
    risk_dist = Counter(assessments[s.id].risk_category for s in clients if s.id in assessments)
    by_industry: dict[str, float] = defaultdict(float)
    for s in clients:
        by_industry[s.industry] += s.outstanding_exposure
    shortfalls = [(s, forecasts[s.id]) for s in clients if s.id in forecasts and forecasts[s.id].shortfall_date]
    scores = [assessments[s.id].model_score for s in clients if s.id in assessments]
    top_leads = sorted([(s, recs[s.id]) for s in smes if s.id in recs and s.onboarding_status != "onboarding" and s.consent_status == "granted" and s.segment == "prospect"], key=lambda x: -x[1].propensity_score)[:5]
    as_of = get_as_of_date()
    activity = [{"id": e.id, "timestamp": e.timestamp.isoformat(timespec="seconds") + "Z", "actor": e.actor, "action": e.action, "category": e.category, "summary": e.summary, "severity": e.severity,
                 "sme_id": e.sme_id, "run_id": e.run_id} for e in db.scalars(select(AuditEvent).where(AuditEvent.is_ai_activity == True).order_by(AuditEvent.timestamp.desc()).limit(12))]  # noqa: E712
    runs = db.execute(select(WorkflowRun.status, func.count()).group_by(WorkflowRun.status)).all()
    return to_jsonable({
        "as_of": as_of.isoformat(), "data_label": "Synthetic demonstration data",
        "kpis": [
            {"key": "clients", "label": "Active SME clients", "value": len(clients), "unit": "count", "hint": f"{len(prospects)} prospects, {len(onboarding)} onboarding"},
            {"key": "exposure", "label": "Outstanding exposure", "value": exposure, "unit": "inr", "hint": f"{len([s for s in clients if s.outstanding_exposure > 0])} borrowing clients"},
            {"key": "avg_score", "label": "Average model score", "value": round(sum(scores) / len(scores)) if scores else None, "unit": "score", "hint": "300-900 synthetic scale" if scores else "Not available - no assessments yet"},
            {"key": "alerts", "label": "Open early-warning alerts", "value": len(alerts), "unit": "count", "hint": f"{len([a for a in alerts if a.priority_level == 'critical'])} critical"},
            {"key": "shortfalls", "label": "Projected shortfalls (60d)", "value": len(shortfalls), "unit": "count", "hint": "from latest baseline forecasts"},
            {"key": "reviews", "label": "Pending human reviews", "value": len(pending), "unit": "count", "hint": f"{len([p for p in pending if p.review_kind == 'kyc_exception'])} KYC, {len([p for p in pending if p.review_kind != 'kyc_exception'])} credit / risk"},
        ],
        "risk_distribution": [{"category": c, "count": risk_dist.get(c, 0)} for c in RISK_ORDER],
        "exposure_by_industry": sorted([{"industry": k, "exposure": v} for k, v in by_industry.items() if v > 0], key=lambda r: -r["exposure"]),
        "kyc_pipeline": [{"status": k, "count": kyc.get(k, 0)} for k in ("in_progress", "info_requested", "exception", "eligible_for_approval", "approved")],
        "alerts_by_level": [{"level": l, "count": len([a for a in alerts if a.priority_level == l])} for l in ("critical", "high", "medium", "low")],
        "top_alerts": [{"id": a.id, "sme_id": a.sme_id, "sme_name": next(s.name for s in smes if s.id == a.sme_id), "priority_score": a.priority_score, "priority_level": a.priority_level,
                        "primary_signal": a.primary_signal, "exposure": a.exposure, "days_to_stress": a.days_to_stress, "review_status": a.review_status} for a in alerts[:6]],
        "shortfalls": sorted([{"sme_id": s.id, "sme_name": s.name, "shortfall_date": f.shortfall_date, "amount": f.shortfall_amount, "days": (f.shortfall_date - as_of).days} for s, f in shortfalls], key=lambda r: r["days"])[:6],
        "top_leads": [{"sme_id": s.id, "sme_name": s.name, "product": r.product, "propensity_score": r.propensity_score, "est_value": r.est_value, "industry": s.industry} for s, r in top_leads],
        "pending_reviews": [{"id": p.id, "sme_id": p.sme_id, "kind": p.review_kind, "created_at": p.created_at.isoformat(timespec="seconds"), "sme_name": next(s.name for s in smes if s.id == p.sme_id)} for p in pending[:6]],
        "ai_activity": activity,
        "workflow_runs": {st: n for st, n in runs},
    })


def customer_360(db: Session, sme: SME, reveal: bool, role: str) -> dict[str, Any]:
    from app.services.views import sme_profile

    as_of = get_as_of_date()
    sid = sme.id
    a = repo.latest_assessment(db, sid)
    fc = repo.latest_forecast(db, sid)
    rec = repo.latest_recommendation(db, sid)
    alert = repo.active_alert(db, sid)
    kyc = repo.latest_kyc(db, sid)
    txns = repo.load_txns(db, sid, as_of, 365)
    fcd = None
    if fc:
        fcd = {"horizon_days": fc.horizon_days, "min_balance": fc.min_balance, "shortfall_date": fc.shortfall_date, "shortfall_amount": fc.shortfall_amount, "expected_ending_balance": fc.expected_ending_balance,
               "series": [{"date": p["date"], "balance": p["balance"], "low": p.get("lower"), "high": p.get("upper")} for p in fc.result["series"]], "created_at": fc.created_at.isoformat(timespec="seconds"), "live": False}
    elif len(txns) >= 20:  # no saved forecast: compute the baseline live (and say so) rather than implying "no shortfall"
        from app.services import analysis

        live = analysis.compute_forecast(repo.get_sme_dict(db, sid), repo.load_txns(db, sid, as_of), as_of, 60, None)
        sm = live["summary"]
        fcd = {"horizon_days": 60, "min_balance": sm["min_balance"], "shortfall_date": sm["shortfall_date"], "shortfall_amount": sm["shortfall_amount"], "expected_ending_balance": sm["expected_ending_balance"],
               "series": [{"date": p["date"], "balance": p["balance"], "low": p.get("lower"), "high": p.get("upper")} for p in live["series"]], "created_at": utcnow().isoformat(timespec="seconds"), "live": True}
    monthly: dict[str, dict] = {}
    for t in txns:
        if t["is_bounced"]:
            continue
        m = monthly.setdefault(t["txn_date"].strftime("%Y-%m"), {"month": t["txn_date"].strftime("%Y-%m"), "inflow": 0.0, "outflow": 0.0})
        m["inflow" if t["direction"] == "in" else "outflow"] += t["amount"]
    bal = {}
    for t in txns:
        bal[t["txn_date"]] = t["balance_after"]
    runs = [{"id": r.id, "template": r.template, "status": r.status, "started_at": r.started_at.isoformat(timespec="seconds") + "Z", "duration_ms": r.duration_ms}
            for r in db.scalars(select(WorkflowRun).where(WorkflowRun.sme_id == sid).order_by(WorkflowRun.started_at.desc()).limit(6))]
    audit = [{"id": e.id, "timestamp": e.timestamp.isoformat(timespec="seconds") + "Z", "actor": e.actor, "role": e.role, "action": e.action, "summary": e.summary, "severity": e.severity}
             for e in db.scalars(select(AuditEvent).where(AuditEvent.sme_id == sid).order_by(AuditEvent.timestamp.desc()).limit(10))]
    cps = Counter()
    for t in txns:
        if t["direction"] == "in" and not t["is_bounced"]:
            cps[t["counterparty"]] += t["amount"]
    total_in = sum(cps.values()) or 1
    pend = [r for r in db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.sme_id == sid, HumanReviewDecision.status == "pending"))]
    return to_jsonable({
        "profile": sme_profile(sme, reveal and role in ("KYC Analyst", "Credit Analyst"), role), "reveal_denied": bool(reveal and role not in ("KYC Analyst", "Credit Analyst")),
        "kyc": ({"id": kyc.id, "status": kyc.status, "exceptions": len(kyc.exceptions or []), "missing": kyc.missing_documents} if kyc else None),
        "credit": ({"id": a.id, "score": a.model_score, "pd": a.pd_estimate, "risk_category": a.risk_category, "policy_outcome": a.policy_outcome, "limit_low": a.limit_low, "limit_high": a.limit_high,
                    "explanation": a.explanation, "human_review_status": a.human_review_status, "created_at": a.created_at.isoformat(timespec="seconds"), "positive_factors": a.positive_factors[:3],
                    "risk_factors": a.risk_factors[:3]} if a else None),
        "forecast": fcd,
        "alert": ({"id": alert.id, "priority_score": alert.priority_score, "priority_level": alert.priority_level, "primary_signal": alert.primary_signal, "recommended_action": alert.recommended_action,
                   "signals": alert.signals, "talking_points": alert.talking_points, "days_to_stress": alert.days_to_stress, "review_status": alert.review_status} if alert else None),
        "recommendation": ({"product": rec.product, "propensity_score": rec.propensity_score, "est_value": rec.est_value, "rationale": rec.rationale, "outreach_action": rec.outreach_action,
                            "outreach_allowed": rec.outreach_allowed, "indicators": rec.indicators} if rec else None),
        "monthly": [monthly[k] for k in sorted(monthly)], "balance_history": [{"date": d.isoformat(), "balance": b} for d, b in sorted(bal.items())][-120:],
        "top_customers": [{"name": k, "share": v / total_in} for k, v in cps.most_common(5)],
        "recent_runs": runs, "audit": audit, "pending_reviews": [{"id": p.id, "kind": p.review_kind, "run_id": p.run_id} for p in pend],
        "next_best_actions": _next_actions(sme, a, fcd, alert, kyc, rec, pend),
    })


def _next_actions(sme, a, fcd, alert, kyc, rec, pend) -> list[dict]:
    acts = []
    if pend:
        acts.append({"label": "Decide the pending human review", "reason": f"{len(pend)} item(s) are waiting for an authorised reviewer.", "target": "reviews"})
    if kyc and kyc.status in ("exception", "info_requested", "in_progress"):
        acts.append({"label": "Resolve KYC exceptions", "reason": f"KYC status is '{kyc.status}'.", "target": "kyc"})
    if alert:
        acts.append({"label": alert.recommended_action, "reason": f"Open {alert.priority_level} alert ({alert.primary_signal.replace('_', ' ')}).", "target": "ews"})
    if fcd and fcd.get("shortfall_date"):
        acts.append({"label": "Review the liquidity plan", "reason": f"Shortfall projected on {fcd['shortfall_date']}.", "target": "cashflow"})
    if rec and rec.outreach_allowed and sme.segment == "client" and not alert:
        acts.append({"label": rec.outreach_action, "reason": rec.rationale, "target": "prospecting"})
    if not acts:
        acts.append({"label": "No action needed", "reason": "No open alerts, reviews or exceptions.", "target": None})
    return acts[:5]
