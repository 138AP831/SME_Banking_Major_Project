"""Module services: pure *compute* functions (used by LangGraph nodes and API routes alike)
and *persist* functions (DB writes + audit/activity events).

Keeping compute and persistence separate means the graph's ``persist_results`` node is a real,
distinct step, while direct API calls (interactive sliders, quick actions) reuse the same logic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.util import inr, to_jsonable
from app.ml import credit as credit_ml
from app.ml import ews as ews_ml
from app.ml import forecast as forecast_ml
from app.ml import propensity as prop_ml
from app.ml.features import build_features, feature_quality
from app.models.entities import (Alert, CashflowForecast, CreditApplication, CreditAssessment, ProductRecommendation, RiskSignal, RMFeedback, new_id, utcnow)
from app.repositories import data as repo
from app.services import policy as policy_svc
from app.services.audit import log_audit


# ----------------------------------------------------------------------------- settings
def credit_model_enabled(db: Session) -> bool:
    from app.models.entities import AppSetting

    row = db.get(AppSetting, "credit_model_available")
    return True if row is None else bool(row.value)


# ----------------------------------------------------------------------------- compute
def default_request(features: dict, app: CreditApplication | None, params: dict | None) -> dict:
    params = params or {}
    if params.get("requested_amount"):
        return {"requested_amount": float(params["requested_amount"]), "tenure_months": int(params.get("tenure_months") or 24),
                "application_id": params.get("credit_application_id"), "source": "request"}
    if app is not None:
        return {"requested_amount": float(app.requested_amount), "tenure_months": int(app.tenure_months), "application_id": app.id, "source": "open_application",
                "purpose": app.purpose, "facility_type": app.facility_type}
    amt = max(500_000.0, round(features["avg_monthly_inflow"] * 1.5 / 50_000) * 50_000)
    return {"requested_amount": amt, "tenure_months": 24, "application_id": None, "source": "illustrative_default",
            "purpose": "Illustrative preliminary assessment (no open application)"}


def compute_credit(sme: dict, features: dict, request: dict, force_fallback: bool = False) -> dict:
    a = credit_ml.assess(features, force_fallback=force_fallback)
    # policy outcome is evaluated separately; the limit range only needs a provisional outcome
    low, high = credit_ml.suggested_limit(features, a["risk_category"], "eligible" if a["risk_category"] != "High" else "refer")
    return {**a, "limit_low": low, "limit_high": high, "request": request,
            "disclaimer": "Preliminary demo assessment from a prototype model trained on synthetic data - not a bank credit decision.",
            "score_scale": "300-900 (higher = lower risk), synthetic scorecard scaling",
            "pd_note": ("Estimated PD comes from an XGBoost model trained on synthetic labels; it is not calibrated to real-world defaults."
                        if a["pd_estimate"] is not None else "Model unavailable - deterministic scorecard fallback in use; no PD is shown.")}


def compute_forecast(sme: dict, txns: list[dict], as_of: date, horizon: int, scenario: dict | None) -> dict:
    headroom = max(0.0, float(sme.get("sanctioned_limit") or 0) * (1 - float(sme.get("utilization") or 0)))
    return forecast_ml.forecast_cashflow(txns, as_of, horizon, scenario, facility_headroom=headroom)


def compute_risk(sme: dict, features: dict, forecast: dict | None, credit: dict | None, feedback: list[dict] | None = None) -> dict:
    sigs = ews_ml.detect_signals(features, forecast)
    pr = ews_ml.prioritize(sme, features, sigs, forecast, credit.get("pd_estimate") if credit else None, feedback)
    return {"signals": sigs, **pr, "talking_points": ews_ml.talking_points(sme, sigs, forecast), "missing_data": ews_ml.missing_data(sme, features),
            "ews_applicable": sme.get("segment") == "client" and sme.get("onboarding_status") == "active"}


def recent_feedback(db: Session, sme_id: str) -> list[dict]:
    rows = db.scalars(select(RMFeedback).where(RMFeedback.sme_id == sme_id).order_by(RMFeedback.created_at.desc()).limit(3)).all()
    return [{"outcome": r.outcome, "action_taken": r.action_taken, "age_days": (utcnow() - r.created_at).days} for r in rows]


# ----------------------------------------------------------------------------- persist
def persist_recommendation(db: Session, sme: dict, run_id: str | None, rec: dict, actor: str = "SMEFlow AI", role: str = "system") -> str:
    row = ProductRecommendation(id=new_id("REC"), sme_id=sme["id"], run_id=run_id, product=rec["product"], propensity_score=rec["propensity_score"], all_scores=rec["scores"],
                                est_value=rec["est_value"], indicators=rec["indicators"], rationale=rec["rationale"], outreach_action=rec["outreach_action"],
                                outreach_allowed=rec["outreach_allowed"])
    db.add(row)
    log_audit(db, actor=actor, role=role, action="prospect_ranked", category="prospecting", entity_type="recommendation", entity_id=row.id, sme_id=sme["id"], run_id=run_id,
              summary=f"{sme['name']} ranked for {rec['product_label']} (propensity {rec['propensity_score']:.0f}/100)", details={"score": rec["propensity_score"], "product": rec["product"]}, is_ai=True)
    return row.id


def _human_status(review: dict | None, policy: dict) -> str:
    if review and review.get("action"):
        return {"approve": "approved_to_proceed", "reject": "declined_by_reviewer", "request_info": "info_requested"}[review["action"]]
    if policy["requires_review"] and policy.get("review_kind") in ("credit_referral", "kyc_exception"):
        return "pending"
    return "not_required"


def persist_credit(db: Session, sme: dict, run_id: str | None, credit: dict, policy: dict, narrative: str, review: dict | None = None,
                   actor: str = "SMEFlow AI", role: str = "system") -> str:
    req = credit["request"]
    hr = _human_status(review, policy)
    row = CreditAssessment(id=new_id("ASM"), sme_id=sme["id"], application_id=req.get("application_id"), run_id=run_id, requested_amount=req["requested_amount"],
                           tenure_months=req["tenure_months"], model_score=credit["model_score"], pd_estimate=credit["pd_estimate"], pd_source=credit["pd_source"],
                           risk_category=credit["risk_category"], policy_outcome=policy["outcome"], policy_rules=to_jsonable(policy["triggered"]),
                           limit_low=credit["limit_low"], limit_high=credit["limit_high"], positive_factors=credit["positive_factors"], risk_factors=credit["risk_factors"],
                           explanation=narrative, explanation_type=credit["explanation_type"], model_version=credit["model_version"], human_review_status=hr,
                           features={k: credit_features_subset(v) for k, v in credit.get("features_used", {}).items()} if credit.get("features_used") else {})
    db.add(row)
    app = db.get(CreditApplication, req["application_id"]) if req.get("application_id") else None
    if app:
        app.latest_assessment_id = row.id
        app.bureau_score = app.bureau_score or sme.get("bureau_score")
        app.updated_at = utcnow()
        if review and review.get("action") == "approve":
            app.status = "recommended_to_proceed"
        elif review and review.get("action") == "reject":
            app.status = "declined_by_reviewer"
        elif review and review.get("action") == "request_info":
            app.status = "info_requested"
        elif policy["outcome"] == "not_eligible":
            app.status = "not_eligible"
        elif policy["requires_review"]:
            app.status = "referred"
        else:
            app.status = "under_review"
    label = {"eligible": "eligible for further consideration", "refer": "referred for human review", "not_eligible": "not eligible under the demo policy"}[policy["outcome"]]
    log_audit(db, actor=actor, role=role, action="credit_assessment_completed", category="credit", entity_type="credit_assessment", entity_id=row.id, sme_id=sme["id"], run_id=run_id,
              summary=f"Credit assessment completed for {sme['name']}: score {credit['model_score']} ({credit['risk_category']}), {label}",
              details={"score": credit["model_score"], "pd": credit["pd_estimate"], "policy_outcome": policy["outcome"], "rules": [t["rule_id"] for t in policy["triggered"]]}, is_ai=True)
    return row.id


def credit_features_subset(v: Any) -> Any:
    return v


def persist_forecast(db: Session, sme: dict, run_id: str | None, fc: dict, actor: str = "SMEFlow AI", role: str = "system", log: bool = True) -> str:
    s = fc["summary"]
    row = CashflowForecast(id=new_id("FC"), sme_id=sme["id"], run_id=run_id, horizon_days=fc["horizon_days"], scenario=fc["scenario"], is_baseline=fc["is_baseline"],
                           expected_ending_balance=s["expected_ending_balance"], min_balance=s["min_balance"],
                           low_balance_date=date.fromisoformat(s["low_balance_date"]) if s["low_balance_date"] else None,
                           shortfall_date=date.fromisoformat(s["shortfall_date"]) if s["shortfall_date"] else None, shortfall_amount=s["shortfall_amount"], result=to_jsonable(fc))
    db.add(row)
    if log and fc["is_baseline"]:
        if s["shortfall_date"]:
            log_audit(db, actor=actor, role=role, action="cashflow_shortfall_forecast", category="forecast", entity_type="forecast", entity_id=row.id, sme_id=sme["id"], run_id=run_id,
                      summary=f"Cash-flow shortfall forecast for {sme['name']}: overdraw of {inr(s['shortfall_amount'])} from {s['shortfall_date']}", severity="attention",
                      details={"days_to_shortfall": s["days_to_shortfall"], "amount": s["shortfall_amount"]}, is_ai=True)
        else:
            log_audit(db, actor=actor, role=role, action="cashflow_forecast_generated", category="forecast", entity_type="forecast", entity_id=row.id, sme_id=sme["id"], run_id=run_id,
                      summary=f"{fc['horizon_days']}-day cash-flow forecast generated for {sme['name']}: no shortfall projected", details={"ending_balance": s["expected_ending_balance"]}, is_ai=True)
    return row.id


def persist_risk(db: Session, sme: dict, run_id: str | None, risk: dict, review: dict | None = None, actor: str = "SMEFlow AI", role: str = "system") -> str | None:
    now = utcnow()
    for sg in risk["signals"]:
        db.add(RiskSignal(id=new_id("SIG"), sme_id=sme["id"], run_id=run_id, signal_type=sg["signal_type"], severity=sg["severity"], description=sg["description"],
                          evidence=sg["evidence"], is_simulated=sg["is_simulated"], detected_at=now))
    if not risk.get("ews_applicable", True) or not risk["needs_alert"]:
        return None
    status = "open"
    if review and review.get("action"):
        status = {"approve": "escalated", "reject": "dismissed", "request_info": "in_review"}[review["action"]]
    existing = repo.active_alert(db, sme["id"])
    fields = dict(run_id=run_id, priority_score=risk["priority_score"], priority_level=risk["priority_level"], components=risk["components"], risk_category=risk["risk_category"],
                  exposure=risk["exposure"], primary_signal=risk["primary_signal"] or "none", signals=[{"signal_type": s["signal_type"], "label": s["label"], "severity": s["severity"],
                                                                                                     "description": s["description"], "is_simulated": s["is_simulated"]} for s in risk["signals"]],
                  days_to_stress=risk["days_to_stress"], recommended_action=risk["recommended_action"], talking_points=risk["talking_points"], missing_data=risk["missing_data"],
                  assigned_rm=sme["assigned_rm"], updated_at=now)
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        if review and review.get("action"):
            existing.review_status = status
        alert, created = existing, False
    else:
        alert = Alert(id=new_id("ALT"), sme_id=sme["id"], review_status=status, created_at=now, **fields)
        db.add(alert)
        created = True
    log_audit(db, actor=actor, role=role, action="risk_alert_raised" if created else "risk_alert_updated", category="alert", entity_type="alert", entity_id=alert.id, sme_id=sme["id"], run_id=run_id,
              summary=(f"High-risk client escalated: {sme['name']}" if status == "escalated" else f"Early-warning alert for {sme['name']}: {risk['priority_level']} priority ({risk['primary_signal']})"),
              severity="critical" if risk["priority_level"] == "critical" else "attention", details={"priority": risk["priority_score"], "components": risk["components"]}, is_ai=True)
    return alert.id


def assess_and_persist_direct(db: Session, sme_id: str, request: dict | None, actor: str, role: str, persist: bool = True) -> dict:
    """Module-level credit assessment without the graph (same compute + policy functions)."""
    from app.core.clock import get_as_of_date
    from app.services.kyc_service import kyc_summary_for_policy

    sme = repo.get_sme_dict(db, sme_id)
    if not sme:
        raise LookupError(sme_id)
    as_of = get_as_of_date()
    txns = repo.load_txns(db, sme_id, as_of)
    app = None
    if request and request.get("credit_application_id"):
        app = db.get(CreditApplication, request["credit_application_id"])
    elif not (request and request.get("requested_amount")):
        app = repo.open_credit_application(db, sme_id)
    base_feat = build_features(sme, txns, as_of)
    req = default_request(base_feat, app, request)
    feats = build_features(sme, txns, as_of, req)
    credit = compute_credit(sme, feats, req, force_fallback=not credit_model_enabled(db))
    fc = compute_forecast(sme, txns, as_of, 60, None)
    kyc = kyc_summary_for_policy(db, sme_id)
    pol = policy_svc.evaluate(workflow_type="credit_assessment", sme=sme, features=feats, credit=credit, kyc=kyc, forecast=fc, risk=None)
    final_low, final_high = credit_ml.suggested_limit(feats, credit["risk_category"], pol["outcome"])
    credit["limit_low"], credit["limit_high"] = final_low, final_high
    narrative = credit_ml.narrative(sme["name"], credit, {"outcome": pol["outcome"], "triggered": pol["triggered"]}, req["requested_amount"])
    out = {"credit": credit, "policy": pol, "narrative": narrative, "features": {k: feats[k] for k in credit_ml.CREDIT_FEATURES}, "kyc": kyc, "forecast_headline": fc["summary"]}
    if persist:
        aid = persist_credit(db, sme, None, credit, pol, narrative, None, actor=actor, role=role)
        out["assessment_id"] = aid
        if pol["requires_review"]:
            from app.services import review as review_svc

            review_svc.create_pending(db, run_id=None, sme_id=sme_id, kind=pol["review_kind"], reasons=pol["review_reasons"], evidence={"policy": pol["triggered"][:6]},
                                      subject_id=req.get("application_id") or aid)
    return out
