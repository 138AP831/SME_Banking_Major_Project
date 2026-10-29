"""Configurable DEMO policy engine - deliberately separate from the models.

Models say how risky something looks; the policy decides what happens next:
    eligible | refer (human review) | not_eligible
Outcomes are never represented as bank-approved lending decisions.
"""
from __future__ import annotations

import copy
from typing import Any

from app.db.session import session_scope
from app.models.entities import AppSetting

POLICY_VERSION = "demo-policy-v1"

DEFAULT_POLICY: dict[str, Any] = {
    "version": POLICY_VERSION,
    "pd_not_eligible": 0.30,
    "pd_refer": 0.10,
    "score_refer_below": 600,          # used when the model is unavailable (scorecard fallback)
    "score_not_eligible_below": 480,
    "bureau_not_eligible_below": 560,
    "bureau_refer_below": 650,
    "utilization_refer_above": 0.85,
    "bounces_refer_at": 3,
    "repayment_delays_refer_at": 2,
    "max_repayment_delay_refer": 10,
    "request_to_inflow_refer_above": 6.0,
    "request_over_limit_factor": 1.25,
    "turnover_conflict_high": 1.35,
    "turnover_conflict_low": 0.65,
    "shortfall_days_refer": 45,
    "escalation_exposure": 10_000_000,   # critical alerts on exposure >= 1 Cr need escalation review
    "forecast_review_exposure": 2_500_000,
}

RULE_CATALOG = [
    ("KYC-001", "KYC must be complete without open exceptions", "kyc_exception"),
    ("CRD-001", "Estimated PD above the not-eligible threshold", None),
    ("CRD-002", "Estimated PD above the referral threshold", "credit_referral"),
    ("CRD-003", "Bureau score below policy floor / missing", "credit_referral"),
    ("CRD-004", "Utilization above the referral limit", "credit_referral"),
    ("CRD-005", "Repeated bounced payments", "credit_referral"),
    ("CRD-006", "Delayed repayments", "credit_referral"),
    ("CRD-007", "Requested amount large relative to receipts / suggested limit", "credit_referral"),
    ("DATA-001", "Declared turnover conflicts with bank-visible receipts", "credit_referral"),
    ("FCST-001", "Projected liquidity shortfall within the review window", "risk_escalation"),
    ("EWS-001", "Critical early-warning alert on a large exposure", "risk_escalation"),
    ("CON-001", "Marketing / data-use consent not granted", None),
]


def get_policy() -> dict[str, Any]:
    try:
        with session_scope() as s:
            row = s.get(AppSetting, "policy_config")
            if row and row.value:
                p = copy.deepcopy(DEFAULT_POLICY)
                p.update(row.value)
                return p
    except Exception:  # noqa: BLE001
        pass
    return copy.deepcopy(DEFAULT_POLICY)


def set_policy(values: dict[str, Any]) -> dict[str, Any]:
    clean = {k: type(DEFAULT_POLICY[k])(v) for k, v in values.items() if k in DEFAULT_POLICY and k != "version"}
    with session_scope() as s:
        row = s.get(AppSetting, "policy_config")
        merged = dict(row.value) if row and row.value else {}
        merged.update(clean)
        if row:
            row.value = merged
        else:
            s.add(AppSetting(key="policy_config", value=merged))
    return get_policy()


def evaluate(*, workflow_type: str, sme: dict, features: dict | None, credit: dict | None, kyc: dict | None, forecast: dict | None,
             risk: dict | None, policy: dict | None = None) -> dict[str, Any]:
    p = policy or get_policy()
    triggered: list[dict] = []
    passed: list[dict] = []
    catalog = {rid: (title, kind) for rid, title, kind in RULE_CATALOG}

    def fire(rid: str, outcome: str, message: str, evidence: dict, review: bool = True):
        title, kind = catalog[rid]
        triggered.append({"rule_id": rid, "title": title, "outcome": outcome, "message": message, "evidence": evidence, "review_kind": kind if review else None, "requires_review": review and outcome == "refer"})

    def ok(rid: str, message: str):
        passed.append({"rule_id": rid, "title": catalog[rid][0], "message": message})

    wants_credit = credit is not None
    # --- KYC (credit and lifecycle)
    if kyc is not None and wants_credit:
        st = kyc.get("status")
        if st in ("approved", "eligible_for_approval"):
            ok("KYC-001", f"KYC status is '{st}'.")
        elif st == "rejected":
            fire("KYC-001", "not_eligible", "KYC was rejected; credit cannot proceed.", {"kyc_status": st, "application_id": kyc.get("application_id")}, review=False)
        else:
            ex = kyc.get("exceptions") or []
            msg = f"KYC is '{st}' ({len(ex)} open exception(s)); credit cannot proceed automatically." if st != "not_started" else "No KYC application exists for this customer."
            fire("KYC-001", "refer", msg, {"kyc_status": st, "application_id": kyc.get("application_id"), "exceptions": [e["message"] for e in ex][:4], "missing": kyc.get("missing_documents", [])})
    if wants_credit and features is not None:
        pd_ = credit.get("pd_estimate")
        score = credit["model_score"]
        if pd_ is not None:
            if pd_ >= p["pd_not_eligible"]:
                fire("CRD-001", "not_eligible", f"Estimated PD {pd_:.1%} is at/above the not-eligible threshold ({p['pd_not_eligible']:.0%}).", {"pd": pd_, "threshold": p["pd_not_eligible"]}, review=False)
            elif pd_ >= p["pd_refer"]:
                fire("CRD-002", "refer", f"Estimated PD {pd_:.1%} is at/above the referral threshold ({p['pd_refer']:.0%}).", {"pd": pd_, "threshold": p["pd_refer"]})
            else:
                ok("CRD-002", f"Estimated PD {pd_:.1%} is below the referral threshold.")
        else:
            if score < p["score_not_eligible_below"]:
                fire("CRD-001", "not_eligible", f"Fallback scorecard {score} is below {p['score_not_eligible_below']}.", {"score": score}, review=False)
            elif score < p["score_refer_below"]:
                fire("CRD-002", "refer", f"Fallback scorecard {score} is below {p['score_refer_below']} (model unavailable - rule-based path).", {"score": score})
        if features["bureau_score_missing"]:
            fire("CRD-003", "refer", "No bureau score on file.", {"bureau_score": None})
        elif features["bureau_score"] < p["bureau_not_eligible_below"]:
            fire("CRD-003", "not_eligible", f"Bureau score {features['bureau_score']} is below the floor of {p['bureau_not_eligible_below']}.", {"bureau_score": features["bureau_score"]}, review=False)
        elif features["bureau_score"] < p["bureau_refer_below"]:
            fire("CRD-003", "refer", f"Bureau score {features['bureau_score']} is below {p['bureau_refer_below']}.", {"bureau_score": features["bureau_score"]})
        else:
            ok("CRD-003", f"Bureau score {features['bureau_score']} meets the policy floor.")
        if features["utilization"] > p["utilization_refer_above"]:
            fire("CRD-004", "refer", f"Utilization {features['utilization']:.0%} exceeds {p['utilization_refer_above']:.0%}.", {"utilization": features["utilization"]})
        if features["bounce_count_90d"] >= p["bounces_refer_at"]:
            fire("CRD-005", "refer", f"{features['bounce_count_90d']} bounced payments in 90 days (limit {p['bounces_refer_at'] - 1}).", {"bounces_90d": features["bounce_count_90d"]})
        if features["repayment_delay_count_6m"] >= p["repayment_delays_refer_at"] or features["max_repayment_delay"] >= p["max_repayment_delay_refer"]:
            fire("CRD-006", "refer", f"{features['repayment_delay_count_6m']} late EMI(s) in 6 months, worst delay {features['max_repayment_delay']} days.",
                 {"late_count": features["repayment_delay_count_6m"], "max_delay": features["max_repayment_delay"]})
        req = features.get("requested_amount", 0)
        if req:
            if features["requested_to_inflow"] > p["request_to_inflow_refer_above"]:
                fire("CRD-007", "refer", f"Requested amount is {features['requested_to_inflow']:.1f}x monthly receipts (limit {p['request_to_inflow_refer_above']:.0f}x).", {"ratio": features["requested_to_inflow"]})
            elif credit.get("limit_high") and req > credit["limit_high"] * p["request_over_limit_factor"] and credit["limit_high"] > 0:
                fire("CRD-007", "refer", f"Requested ₹{req / 1e5:.1f} L exceeds the suggested limit range (up to ₹{credit['limit_high'] / 1e5:.1f} L).", {"requested": req, "limit_high": credit["limit_high"]})
        r = features["declared_to_observed_turnover"]
        if r and (r > p["turnover_conflict_high"] or r < p["turnover_conflict_low"]):
            fire("DATA-001", "refer", f"Declared turnover is {r:.2f}x the bank-visible annualised receipts (conflicting financial data).", {"ratio": r})
        else:
            ok("DATA-001", "Declared turnover is consistent with observed receipts.")
    # --- forecast-driven escalation
    if forecast is not None and workflow_type in ("full_lifecycle", "cashflow_ews", "credit_assessment"):
        d = forecast["summary"]["days_to_shortfall"]
        exposure = float(sme.get("outstanding_exposure") or 0)
        if d is not None and d <= p["shortfall_days_refer"] and (wants_credit or exposure >= p["forecast_review_exposure"]):
            fire("FCST-001", "refer", f"Liquidity shortfall projected in {d} days ({forecast['summary']['shortfall_date']}); needs reviewer attention.",
                 {"days_to_shortfall": d, "shortfall_amount": forecast["summary"]["shortfall_amount"], "exposure": exposure})
    if risk is not None and risk.get("needs_alert") and workflow_type in ("full_lifecycle", "cashflow_ews"):
        if risk["priority_level"] == "critical" and float(sme.get("outstanding_exposure") or 0) >= p["escalation_exposure"]:
            fire("EWS-001", "refer", f"Critical alert (priority {risk['priority_score']:.0f}) on exposure ₹{float(sme.get('outstanding_exposure') or 0) / 1e7:.2f} Cr requires escalation review.",
                 {"priority": risk["priority_score"], "exposure": sme.get("outstanding_exposure"), "primary_signal": risk.get("primary_signal")})
    # --- consent (prospecting / outreach)
    consent = sme.get("consent_status", "granted")
    if consent != "granted":
        fire("CON-001", "not_eligible" if workflow_type == "prospecting" else "info", f"Marketing / data-use consent is '{consent}'. Outreach is blocked until consent is granted.", {"consent_status": consent}, review=False)
    else:
        ok("CON-001", "Marketing / data-use consent granted.")

    outcomes = [t["outcome"] for t in triggered if t["outcome"] != "info"]
    outcome = "not_eligible" if "not_eligible" in outcomes else ("refer" if "refer" in outcomes else "eligible")
    review_rules = [t for t in triggered if t["requires_review"]]
    review_kind = None
    if outcome != "not_eligible" and review_rules:
        for pref in ("kyc_exception", "credit_referral", "risk_escalation"):
            if any(r["review_kind"] == pref for r in review_rules):
                review_kind = pref
                break
    return {
        "policy_version": p["version"], "outcome": outcome, "triggered": triggered, "passed": passed, "requires_review": review_kind is not None, "review_kind": review_kind,
        "review_reasons": review_rules if review_kind else [],  # all rules needing a human, whichever kind routes the review
        "disclaimer": "Demo policy outcome - not a bank credit decision.",
    }


ALLOWED_ROLES = {
    "kyc_exception": ["KYC Analyst"],
    "credit_referral": ["Credit Analyst", "Risk Manager"],
    "risk_escalation": ["Relationship Manager", "Credit Analyst", "Risk Manager"],
}
