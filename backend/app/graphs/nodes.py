"""The 11 LangGraph nodes. Compute nodes are pure w.r.t. the DB (they read, never write);
only ``persist_results`` and the review gate write - so a failed/cancelled run leaves no half results."""
from __future__ import annotations

from datetime import date
from typing import Any

from langgraph.types import interrupt
from sqlalchemy import select

from app.core.util import inr, to_jsonable
from app.db.session import session_scope
from app.ml import credit as credit_ml
from app.ml import propensity as prop_ml
from app.ml.features import build_features, feature_quality
from app.models.entities import HumanReviewDecision
from app.repositories import data as repo
from app.services import analysis, llm
from app.services import policy as policy_svc
from app.services import review as review_svc
from app.services.audit import log_audit
from app.services.kyc_service import kyc_summary_for_policy

CREDIT_TEMPLATES = ("full_lifecycle", "credit_assessment")


def _rehydrate(fn):
    """State is JSON-serialised by the checkpointer; restore the date fields the ML code expects."""
    def inner(s: dict) -> dict:
        sme = s.get("sme")
        if sme and isinstance(sme.get("incorporation_date"), str):
            s = {**s, "sme": {**sme, "incorporation_date": date.fromisoformat(sme["incorporation_date"][:10])}}
        return fn(s)
    inner.__name__ = fn.__name__
    return inner


def _asof(s: dict) -> date:
    return date.fromisoformat(s["as_of"])


def _params(s: dict) -> dict:
    return s.get("params") or {}


def load_sme_profile(s: dict) -> dict:
    with session_scope() as db:
        sme = repo.get_sme_dict(db, s["sme_id"])
        if not sme:
            raise LookupError(f"SME {s['sme_id']} not found")
        txns = repo.load_txns(db, s["sme_id"], _asof(s))
        kyc = kyc_summary_for_policy(db, s["sme_id"])
        req = repo.open_credit_application(db, s["sme_id"])
    sme = to_jsonable(sme)
    return {"sme": sme, "kyc": to_jsonable(kyc), "_metrics": {"transactions": len(txns), "kyc_status": kyc["status"], "consent": sme["consent_status"]}}


def validate_data(s: dict) -> dict:
    with session_scope() as db:
        txns = repo.load_txns(db, s["sme_id"], _asof(s))
    f = build_features(s["sme"], txns, _asof(s))
    q = feature_quality(f)
    warnings = [i["message"] for i in q["issues"]]
    if q["blocking"] and s["template"] != "prospecting":
        # not a crash: the policy/explanation steps report the limitation and the case goes to a human where applicable
        warnings.append("Blocking data-quality issue: results below are limited and flagged.")
    return {"data_quality": {**q, "transactions": len(txns), "history_days": f["data_days"], "checked_at": s["as_of"]}, "warnings": warnings,
            "_metrics": {"issues": len(q["issues"]), "blocking": q["blocking"]}}


def build_shared_features(s: dict) -> dict:
    with session_scope() as db:
        txns = repo.load_txns(db, s["sme_id"], _asof(s))
        app = repo.open_credit_application(db, s["sme_id"]) if s["template"] in CREDIT_TEMPLATES else None
    base = build_features(s["sme"], txns, _asof(s))
    req = analysis.default_request(base, app, _params(s)) if s["template"] in CREDIT_TEMPLATES else None
    feats = build_features(s["sme"], txns, _asof(s), req) if req else base
    out = {"features": to_jsonable(feats), "_metrics": {"feature_count": len(feats)}}
    if req:
        out["params"] = {**_params(s), "_request": req}
    return out


def run_propensity_analysis(s: dict) -> dict:
    rec = prop_ml.recommend(s["features"], s["sme"])
    return {"propensity": to_jsonable(rec), "_metrics": {"top_product": rec["product"], "score": rec["propensity_score"], "method": rec["method"]}}


def run_credit_assessment(s: dict) -> dict:
    p = _params(s)
    req = p.get("_request") or analysis.default_request(s["features"], None, p)
    with session_scope() as db:
        fallback = not analysis.credit_model_enabled(db)
    credit = analysis.compute_credit(s["sme"], s["features"], req, force_fallback=fallback)
    credit["features_used"] = {k: s["features"].get(k) for k in credit_ml.CREDIT_FEATURES}
    return {"credit": to_jsonable(credit), "_metrics": {"score": credit["model_score"], "risk_category": credit["risk_category"], "pd": credit["pd_estimate"],
                                                        "explanation": credit["explanation_type"], "model": credit["model_version"]}}


def forecast_cash_flow(s: dict) -> dict:
    p = _params(s)
    with session_scope() as db:
        txns = repo.load_txns(db, s["sme_id"], _asof(s))
    fc = analysis.compute_forecast(s["sme"], txns, _asof(s), int(p.get("horizon_days") or 60), p.get("scenario"))
    sm = fc["summary"]
    return {"forecast": to_jsonable(fc), "_metrics": {"days_to_shortfall": sm["days_to_shortfall"], "min_balance": sm["min_balance"], "horizon_days": int(p.get("horizon_days") or 60)}}


def analyze_risk_signals(s: dict) -> dict:
    with session_scope() as db:
        fb = analysis.recent_feedback(db, s["sme_id"])
    risk = analysis.compute_risk(s["sme"], s["features"], s.get("forecast"), s.get("credit"), fb)
    return {"risk": to_jsonable(risk), "_metrics": {"signals": len(risk["signals"]), "priority": risk["priority_score"], "level": risk["priority_level"], "alert": risk["needs_alert"]}}


def evaluate_policy_rules(s: dict) -> dict:
    t = s["template"]
    wf = {"full_lifecycle": "full_lifecycle", "credit_assessment": "credit_assessment", "cashflow_ews": "cashflow_ews", "prospecting": "prospecting", "kyc_onboarding": "kyc_onboarding"}[t]
    kyc = s["kyc"] if t in CREDIT_TEMPLATES or t == "kyc_onboarding" else None
    pol = policy_svc.evaluate(workflow_type=wf, sme=s["sme"], features=s["features"], credit=s.get("credit") if t in CREDIT_TEMPLATES else None, kyc=kyc,
                              forecast=s.get("forecast"), risk=s.get("risk"))
    if t == "kyc_onboarding":
        st = s["kyc"]["status"]
        if st in ("exception", "info_requested", "in_progress", "not_started") and not any(x["rule_id"] == "KYC-001" for x in pol["triggered"]):
            ex = s["kyc"].get("exceptions") or []
            if st == "exception" or ex:
                r = {"rule_id": "KYC-001", "title": "KYC must be complete without open exceptions", "outcome": "refer", "review_kind": "kyc_exception", "requires_review": True,
                     "message": f"KYC has {len(ex)} open exception(s) that need an analyst decision.", "evidence": {"exceptions": [e['message'] for e in ex][:5], "kyc_status": st}}
                pol["triggered"].append(r)
                pol.update(outcome="refer", requires_review=True, review_kind="kyc_exception", review_reasons=[r])
            else:
                pol["triggered"].append({"rule_id": "KYC-001", "title": "KYC must be complete without open exceptions", "outcome": "info", "review_kind": None, "requires_review": False,
                                         "message": f"KYC is '{st}' - waiting for documents; nothing to review yet.", "evidence": {"missing": s["kyc"].get("missing_documents")}})
        elif st in ("eligible_for_approval", "approved"):
            pol["passed"].append({"rule_id": "KYC-001", "title": "KYC must be complete without open exceptions", "message": f"KYC status is '{st}'."})
    if s.get("data_quality", {}).get("blocking") and t in CREDIT_TEMPLATES and pol["outcome"] == "eligible":
        r = {"rule_id": "DATA-001", "title": "Data quality", "outcome": "refer", "review_kind": "credit_referral", "requires_review": True,
             "message": "Transaction history is too thin for an automated assessment.", "evidence": s["data_quality"]["issues"]}
        pol["triggered"].append(r)
        pol.update(outcome="refer", requires_review=True, review_kind="credit_referral", review_reasons=[r])
    if t in CREDIT_TEMPLATES and s.get("credit"):
        low, high = credit_ml.suggested_limit(s["features"], s["credit"]["risk_category"], pol["outcome"])
        pol["suggested_limit"] = {"low": low, "high": high}
    return {"policy": to_jsonable(pol), "_metrics": {"outcome": pol["outcome"], "rules_triggered": [r["rule_id"] for r in pol["triggered"]], "requires_review": pol["requires_review"]}}


def human_review_gate(s: dict) -> dict:
    pol = s["policy"]
    kind = pol["review_kind"]
    with session_scope() as db:
        row = db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.run_id == s["run_id"], HumanReviewDecision.review_kind == kind)).first()
        if row is None:
            subject = (s["kyc"].get("application_id") if kind == "kyc_exception" else (s.get("params", {}).get("_request") or {}).get("application_id"))
            row = review_svc.create_pending(db, run_id=s["run_id"], sme_id=s["sme_id"], kind=kind, reasons=pol["review_reasons"],
                                            evidence={"policy": [{k: r[k] for k in ("rule_id", "message", "evidence")} for r in pol["triggered"]][:8]}, subject_id=subject)
        review_id = row.id
    decision = interrupt({"review_id": review_id, "kind": kind, "reasons": [r["message"] for r in pol["review_reasons"]], "allowed_roles": policy_svc.ALLOWED_ROLES[kind],
                          "message": "Workflow paused for a human decision. It will not proceed automatically."})
    return {"review": to_jsonable(decision), "_metrics": {"action": decision.get("action"), "reviewer_role": decision.get("role")}}


def generate_explanation(s: dict) -> dict:
    t, sme, pol = s["template"], s["sme"], s["policy"]
    parts: list[str] = []
    credit_text = None
    if s.get("credit"):
        credit_text = credit_ml.narrative(sme["name"], s["credit"], {"outcome": pol["outcome"], "triggered": pol["triggered"]}, s["credit"]["request"]["requested_amount"])
        parts.append(credit_text)
    if s.get("forecast"):
        sm = s["forecast"]["summary"]
        parts.append(f"Cash-flow outlook ({s['forecast']['horizon_days']} days): " + (f"a shortfall is projected in {sm['days_to_shortfall']} days ({sm['shortfall_date']}), about {inr(sm['shortfall_amount'])}."
                                                                                        if sm["days_to_shortfall"] is not None else f"no shortfall projected; lowest expected balance {inr(sm['min_balance'])}."))
    if s.get("risk") and s["risk"]["signals"]:
        parts.append("Early-warning signals: " + "; ".join(str(x["description"]).strip().rstrip(".") for x in s["risk"]["signals"][:3]) + f". Priority {s['risk']['priority_score']:.0f}/100 ({s['risk']['priority_level']}).")
    if s.get("propensity"):
        pr = s["propensity"]
        parts.append(f"Next-best product: {pr['product_label']} (propensity {pr['propensity_score']:.0f}/100, uncalibrated ranking score). {pr['rationale']}")
    if t == "kyc_onboarding":
        k = s["kyc"]
        parts.append(f"KYC status is '{k['status']}' with {len(k.get('exceptions') or [])} open exception(s) and {len(k.get('missing_documents') or [])} missing document(s). "
                     "External registry checks are SIMULATED placeholders in this prototype.")
    rv = s.get("review")
    if rv and rv.get("action"):
        parts.append(f"Human review: {rv['role']} ({rv['reviewer']}) chose '{rv['action']}' - reason recorded: {rv['reason']}")
    elif pol["requires_review"]:
        parts.append("A human review was required.")
    if s.get("warnings"):
        parts.append("Data notes: " + " ".join(s["warnings"][:3]))
    text = " ".join(parts) or "No analysis modules produced output for this template."
    text, provider = llm.polish(text)
    return {"explanation": {"text": text, "credit_text": credit_text, "llm_provider": provider, "method": "deterministic factor-based narrative" + (f" rephrased by {provider}" if provider else ""), "disclaimer": pol["disclaimer"]},
            "_metrics": {"llm": provider or "none"}}


def persist_results(s: dict) -> dict:
    run_id, sme, rv = s["run_id"], s["sme"], s.get("review")
    ids: dict[str, Any] = {}
    with session_scope() as db:
        if s.get("propensity"):
            ids["recommendation_id"] = analysis.persist_recommendation(db, sme, run_id, s["propensity"])
        if s.get("credit"):
            ids["assessment_id"] = analysis.persist_credit(db, sme, run_id, s["credit"], s["policy"], s["explanation"].get("credit_text") or s["explanation"]["text"], rv if (rv or {}).get("kind") == "credit_referral" else None)
        if s.get("forecast") and s["template"] in ("full_lifecycle", "cashflow_ews") and not _params(s).get("scenario"):
            ids["forecast_id"] = analysis.persist_forecast(db, sme, run_id, s["forecast"])
        if s.get("risk"):
            ids["alert_id"] = analysis.persist_risk(db, sme, run_id, s["risk"], rv if (rv or {}).get("kind") == "risk_escalation" else None)
        log_audit(db, actor="SMEFlow AI", role="system", action="workflow_results_persisted", category="workflow", entity_type="workflow_run", entity_id=run_id, sme_id=sme["id"], run_id=run_id,
                  summary=f"Workflow '{s['template']}' results persisted for {sme['name']} (policy outcome: {s['policy']['outcome']})", details=ids, is_ai=True)
    return {"persisted": ids, "_metrics": {"records": len([v for v in ids.values() if v])}}


_RAW = {
    "load_sme_profile": (load_sme_profile, ("sme_id", "as_of")),
    "validate_data": (validate_data, ("sme", "kyc")),
    "build_shared_features": (build_shared_features, ("sme", "data_quality")),
    "run_propensity_analysis": (run_propensity_analysis, ("features",)),
    "run_credit_assessment": (run_credit_assessment, ("features", "params")),
    "forecast_cash_flow": (forecast_cash_flow, ("params", "as_of")),
    "analyze_risk_signals": (analyze_risk_signals, ("features", "forecast", "credit")),
    "evaluate_policy_rules": (evaluate_policy_rules, ("credit", "kyc", "risk", "forecast")),
    "human_review_gate": (human_review_gate, ("policy",)),
    "generate_explanation": (generate_explanation, ("policy", "review")),
    "persist_results": (persist_results, ("explanation", "review")),
}

NODE_META = {
    "load_sme_profile": {"label": "Load SME profile", "type": "data", "description": "Reads the SME master record, KYC status, consent and transaction count from the shared data layer."},
    "validate_data": {"label": "Validate data", "type": "validation", "description": "Checks history length, transaction volume, bureau availability and conflicting turnover figures."},
    "build_shared_features": {"label": "Build shared features", "type": "features", "description": "Builds one feature set (cash-flow, liquidity, repayment, utilisation) reused by every downstream module."},
    "run_propensity_analysis": {"label": "Propensity analysis", "type": "model", "description": "Ranks next-best products with a one-vs-rest model trained on synthetic labels (consent-gated outreach)."},
    "run_credit_assessment": {"label": "Credit assessment", "type": "model", "description": "XGBoost PD estimate, 300-900 score and SHAP factors; deterministic scorecard when the model is unavailable."},
    "forecast_cash_flow": {"label": "Cash-flow forecast", "type": "model", "description": "Transparent baseline forecast (weekday profile + recurring obligations) with scenario levers and a heuristic band."},
    "analyze_risk_signals": {"label": "Risk signals (EWS)", "type": "model", "description": "Detects early-warning signals and ranks them by explainable priority = risk + exposure + urgency + actionability."},
    "evaluate_policy_rules": {"label": "Policy rules", "type": "policy", "description": "Configurable demo policy engine, separate from the models: eligible / refer / not eligible."},
    "human_review_gate": {"label": "Human review gate", "type": "human", "description": "Pauses the graph with interrupt() until an authorised reviewer decides. Never auto-approves."},
    "generate_explanation": {"label": "Generate explanation", "type": "explain", "description": "Plain-language explanation from the actual factors; an optional LLM may only rephrase it."},
    "persist_results": {"label": "Persist results", "type": "data", "description": "Writes assessments, forecasts, alerts, recommendations and the audit trail."},
}


NODES = {k: (_rehydrate(f), keys) for k, (f, keys) in _RAW.items()}
