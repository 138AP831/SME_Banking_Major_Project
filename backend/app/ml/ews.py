"""Early Warning System: explainable risk signals + priority ranking.

priority = risk (0-40) + exposure (0-25) + urgency (0-25) + actionability (0-10)
Every component is returned so an RM can see WHY a client ranks where it does. Nothing here is a
validated probability of default.
"""
from __future__ import annotations

import math
from typing import Any

from app.core.util import clamp

SIGNAL_LABELS = {
    "forecast_shortfall": "Forecast liquidity shortfall", "delayed_repayments": "Delayed loan repayments", "rising_utilization": "Rising credit utilization",
    "bounced_payments": "Bounced payments", "declining_turnover": "Declining turnover", "declining_balance": "Declining account balance",
    "reduced_activity": "Reduced transaction activity", "receivable_delay": "Slowing customer payments",
}
SIMULATED_SIGNALS = {"rising_utilization"}  # derived from facility data that is simulated in this prototype


def detect_signals(f: dict[str, Any], forecast: dict | None) -> list[dict]:
    sig: list[dict] = []

    def add(t: str, sev: float, desc: str, evidence: dict) -> None:
        sig.append({"signal_type": t, "label": SIGNAL_LABELS[t], "severity": round(clamp(sev, 0, 1), 3), "description": desc,
                    "evidence": evidence, "is_simulated": t in SIMULATED_SIGNALS})

    if forecast:
        s = forecast["summary"]
        if s["days_to_shortfall"] is not None and s["days_to_shortfall"] <= 90:
            d = s["days_to_shortfall"]
            add("forecast_shortfall", clamp(1.1 - d / 60, 0.4, 1.0),
                f"Projected balance turns negative in {d} days ({s['shortfall_date']}), low point −₹{s['shortfall_amount'] / 1e5:.1f} L.",
                {"days_to_shortfall": d, "shortfall_date": s["shortfall_date"], "shortfall_amount": s["shortfall_amount"], "horizon_days": forecast["horizon_days"]})
        elif s["days_to_low_balance"] is not None and s["days_to_low_balance"] <= 30:
            add("forecast_shortfall", 0.3, f"Balance expected to fall below the 7-day cash buffer in {s['days_to_low_balance']} days.",
                {"days_to_low_balance": s["days_to_low_balance"], "buffer": forecast["buffer_threshold"]})
    ch = f["balance_change_30d_pct"]
    if ch <= -0.25 and f["balance_30d_ago"] > 0:
        add("declining_balance", (-ch - 0.2) / 0.6, f"Account balance fell {-ch:.0%} in 30 days (₹{f['balance_30d_ago'] / 1e5:.1f} L → ₹{f['current_balance'] / 1e5:.1f} L).",
            {"balance_now": f["current_balance"], "balance_30d_ago": f["balance_30d_ago"], "change_pct": round(ch * 100, 1)})
    if f.get("has_facility_history") and (f["utilization_change_90d"] >= 0.10 or f["utilization"] >= 0.85):
        sev = 0.5 * clamp(f["utilization_change_90d"] / 0.3, 0, 1) + 0.5 * clamp((f["utilization"] - 0.6) / 0.35, 0, 1)
        add("rising_utilization", sev, f"Utilization is {f['utilization']:.0%}, up {f['utilization_change_90d']:.0%} points over 90 days (simulated facility feed).",
            {"utilization": f["utilization"], "change_90d": f["utilization_change_90d"]})
    if f["bounce_count_90d"] >= 1:
        add("bounced_payments", 0.25 * f["bounce_count_90d"], f"{f['bounce_count_90d']} payment(s) returned/bounced in the last 90 days.",
            {"count_90d": f["bounce_count_90d"], "rate": f["bounce_rate_90d"]})
    if f["repayment_delay_count_6m"] >= 1 and f["max_repayment_delay"] >= 3:
        add("delayed_repayments", 0.2 * f["repayment_delay_count_6m"] + 0.03 * f["max_repayment_delay"],
            f"{f['repayment_delay_count_6m']} EMI(s) paid late in 6 months (worst: {f['max_repayment_delay']} days; recent delays {f['repayment_delays_recent']}).",
            {"late_count_6m": f["repayment_delay_count_6m"], "max_delay_days": f["max_repayment_delay"], "recent": f["repayment_delays_recent"]})
    if f["inflow_trend_pct"] <= -3 or f["inflow_last30_vs_prev60_pct"] <= -15:
        sev = max((-f["inflow_trend_pct"] - 2) / 8, (-f["inflow_last30_vs_prev60_pct"] - 10) / 30)
        add("declining_turnover", sev, f"Receipts trend {f['inflow_trend_pct']:+.1f}%/month; the last 30 days are {f['inflow_last30_vs_prev60_pct']:+.0f}% versus the prior two months.",
            {"trend_pct_per_month": f["inflow_trend_pct"], "last30_vs_prev60_pct": f["inflow_last30_vs_prev60_pct"], "monthly_inflows": f["monthly_inflows"][:4]})
    if f["activity_change_pct"] <= -0.25:
        add("reduced_activity", (-f["activity_change_pct"] - 0.2) / 0.5, f"Transaction count is {f['activity_change_pct']:.0%} versus the prior 60-day average.",
            {"txn_count_30d": f["txn_count_30d"], "change_pct": round(f["activity_change_pct"] * 100, 1)})
    if f["receipt_delay_change"] >= 6 or f["avg_receipt_delay_30d"] >= 30:
        add("receivable_delay", max(f["receipt_delay_change"] / 20, (f["avg_receipt_delay_30d"] - 25) / 25),
            f"Customers now pay {f['avg_receipt_delay_30d']:.0f} days late on average ({f['receipt_delay_change']:+.0f} days versus the prior 2 months).",
            {"avg_delay_30d": f["avg_receipt_delay_30d"], "avg_delay_prev": f["avg_receipt_delay_prev"]})
    sig.sort(key=lambda x: -x["severity"])
    return sig


def risk_category(score: float) -> str:
    return "High" if score >= 65 else "Elevated" if score >= 40 else "Moderate" if score >= 20 else "Low"


ACTIONS = {
    "forecast_shortfall": "Discuss short-term liquidity support (invoice financing / working-capital top-up) before the projected shortfall date",
    "delayed_repayments": "Contact the customer about delayed EMI payments and review restructuring options",
    "rising_utilization": "Review the credit limit and utilization trend with a credit analyst",
    "bounced_payments": "Call the customer about returned payments and check account conduct",
    "declining_turnover": "Schedule a customer check-in on the order book and turnover trend",
    "declining_balance": "Schedule a check-in to review the cash buffer",
    "reduced_activity": "Schedule a customer check-in to understand the drop in activity",
    "receivable_delay": "Discuss delayed receivables and offer invoice financing",
}


def talking_points(sme: dict, signals: list[dict], forecast: dict | None) -> list[str]:
    pts = [f"Open with the relationship: {sme['name']} has banked with us in {sme['city']}; ask how trading has been recently."]
    for s in signals[:3]:
        pts.append({
            "forecast_shortfall": "Our cash-flow projection shows pressure on liquidity - ask about expected receipts and whether any large payments are due, and walk through the bridging options.",
            "delayed_repayments": "Our records show recent EMIs arriving later than scheduled - ask whether there is a timing or cash problem and whether a revised schedule would help.",
            "rising_utilization": "The working-capital line is being drawn more heavily - understand what is driving the usage (inventory build, slow collections, new contracts).",
            "bounced_payments": "Some payments were returned recently - ask about the cause and offer an overdraft buffer if the issue is timing.",
            "declining_turnover": "Receipts are lower than the prior months - ask about order book, seasonality or lost customers.",
            "declining_balance": "Average balances have fallen quickly - confirm there is no one-off outflow and discuss the buffer.",
            "reduced_activity": "Transaction activity has dropped - ask whether volumes are shifting to another bank.",
            "receivable_delay": "Customers are paying slower - identify the largest overdue buyers and discuss invoice discounting.",
        }[s["signal_type"]] + f" (Evidence: {s['description']})")
    if forecast and forecast["summary"]["days_to_shortfall"] is not None:
        pts.append(f"Agree a follow-up date before {forecast['summary']['shortfall_date']} and, if needed, loop in a credit analyst.")
    return pts


def prioritize(sme: dict, f: dict[str, Any], signals: list[dict], forecast: dict | None, pd_estimate: float | None, recent_feedback: list[dict] | None = None) -> dict[str, Any]:
    max_sev = max((s["severity"] for s in signals), default=0.0)
    sig_risk = 100 * (1 - math.prod(1 - 0.8 * s["severity"] for s in signals)) if signals else 0.0
    risk_score = 0.75 * sig_risk + 0.25 * min(100.0, (pd_estimate or 0.0) * 250) if pd_estimate is not None else sig_risk
    exposure = float(sme.get("outstanding_exposure") or 0.0)
    exposure_c = 25 * min(1.0, math.log10(1 + exposure / 1e5) / math.log10(1 + 2.5e7 / 1e5)) if exposure > 0 else 0.0
    d = None
    if forecast:
        d = forecast["summary"]["days_to_shortfall"] if forecast["summary"]["days_to_shortfall"] is not None else forecast["summary"]["days_to_low_balance"]
    if d is not None:
        u = 1.0 if d <= 14 else 0.8 if d <= 30 else 0.55 if d <= 60 else 0.3 if d <= 90 else 0.0
    else:
        u = min(0.4, max_sev * 0.4)
    act = 0.7 + (0.3 if signals else 0.0)
    if not sme.get("assigned_rm"):
        act -= 0.3
    if recent_feedback:
        latest = recent_feedback[0]
        if latest.get("outcome") in ("resolved", "improving") and latest.get("age_days", 99) <= 14:
            act = 0.3
    comps = {"risk": round(0.4 * risk_score, 1), "exposure": round(exposure_c, 1), "urgency": round(25 * u, 1), "actionability": round(10 * clamp(act, 0, 1), 1)}
    score = round(sum(comps.values()), 1)
    level = "critical" if score >= 65 else "high" if score >= 50 else "medium" if score >= 35 else "low"
    primary = signals[0]["signal_type"] if signals else None
    action = ACTIONS.get(primary, "No action required - continue routine monitoring")
    if primary == "forecast_shortfall" and forecast and forecast["summary"]["shortfall_date"]:
        action = f"{action} ({forecast['summary']['shortfall_date']})"
    return {
        "risk_score": round(risk_score, 1), "risk_category": risk_category(risk_score), "priority_score": score, "priority_level": level, "components": comps,
        "component_max": {"risk": 40, "exposure": 25, "urgency": 25, "actionability": 10}, "primary_signal": primary, "days_to_stress": d,
        "recommended_action": action, "exposure": exposure,
        "needs_alert": level != "low" or risk_category(risk_score) in ("Elevated", "High"),
    }


def missing_data(sme: dict, f: dict[str, Any]) -> list[str]:
    miss = []
    if not f.get("has_facility_history"):
        miss.append("No facility / utilization history (not a lending client yet) - utilization signals unavailable.")
    if f["bureau_score_missing"]:
        miss.append("No bureau score on file.")
    if f["data_days"] < 90:
        miss.append(f"Only {f['data_days']} days of transactions - trend signals are unreliable.")
    miss.append("Utilization history is SIMULATED in this prototype (no live facility feed).")
    return miss
