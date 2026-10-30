"""Customer-side indicative loan readiness and improvement plan.

Deliberately independent of the bank's credit model, internal score, policy thresholds and alerts: these are ILLUSTRATIVE,
customer-facing criteria (``CRITERIA_VERSION``) that help an SME understand where it stands. The output is an indicative
view, not a loan approval and not a guarantee of eligibility.
"""
from __future__ import annotations

from typing import Any

CRITERIA_VERSION = "illustrative-readiness-v1"
DISCLAIMER = ("Indicative assessment on synthetic demo data using illustrative criteria. It is not a loan approval, not a credit decision and not a guarantee of eligibility. "
              "The bank makes its own decision after a human review.")
STATUS_LABEL = {"likely_ready": "Likely ready (indicative)", "nearly_ready": "Nearly ready (indicative)", "needs_improvement": "Needs improvement (indicative)",
                "more_information_needed": "More information needed"}
WEIGHTS = {"cashflow_consistency": 18, "liabilities_burden": 18, "repayment_regularity": 18, "working_capital_buffer": 14, "revenue_diversification": 10,
           "documentation_completeness": 12, "financial_records_consistency": 10}

PLAN_CATALOGUE: dict[str, dict[str, str]] = {
    "cashflow_consistency": {"title": "Make cash inflows more regular", "why": "Lenders look for predictable income. Big swings in weekly receipts make it harder to see that instalments will be covered.",
                             "how": "Invoice on fixed cycles, chase overdue receivables early, and agree advance or milestone payments with your largest customers."},
    "liabilities_burden": {"title": "Reduce monthly debt obligations relative to income", "why": "When a large share of monthly inflow already goes to loan instalments, there is less room to take on a new one comfortably.",
                           "how": "Prepay or consolidate your most expensive loans and avoid new short-term borrowing in the months before you apply."},
    "repayment_regularity": {"title": "Improve repayment regularity", "why": "Late or returned payments are the clearest signal a lender reads about future repayment behaviour.",
                             "how": "Set up auto-debit with a cash buffer a day before each due date, and clear any returned payments first."},
    "working_capital_buffer": {"title": "Maintain sufficient working capital", "why": "A cash buffer shows you can absorb delays in customer payments without missing obligations.",
                               "how": "Aim for about a month of operating cash, stagger large payments, and keep a separate reserve balance."},
    "revenue_diversification": {"title": "Diversify customer revenue sources", "why": "Relying on one or two customers means a single delay or loss can disrupt the whole cash cycle.",
                                "how": "Add customers in adjacent segments, set a target share cap per customer, and track the top-customer share every month."},
    "documentation_completeness": {"title": "Maintain better financial documentation", "why": "Complete, current documents let a lender verify your business quickly and reduce back-and-forth.",
                                   "how": "Upload any missing KYC documents and refresh your submitted financial figures at least every quarter."},
    "financial_records_consistency": {"title": "Reconcile declared and bank-visible turnover", "why": "Large gaps between the turnover you declare and what your account shows raise questions during verification.",
                                      "how": "Route business receipts through the business account and update your declared turnover or note the reasons for the difference."},
}


def _ind(code: str, label: str, status: str, value: str, target: str, explanation: str) -> dict[str, Any]:
    return {"code": code, "label": label, "status": status, "value": value, "target": target, "explanation": explanation, "weight": WEIGHTS[code]}


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def compute_readiness(*, features: dict[str, Any], forecast: dict[str, Any] | None, submission: dict[str, Any] | None, docs: dict[str, Any] | None,
                      declared_turnover: float | None, as_of: str) -> dict[str, Any]:
    f = features
    inds: list[dict[str, Any]] = []
    # 1. cash-flow consistency
    cv, trend = float(f.get("inflow_cv", 0)), float(f.get("inflow_trend_pct", 0))
    st = "good" if cv <= 0.45 and trend > -3 else "watch" if cv <= 0.8 and trend > -8 else "gap"
    inds.append(_ind("cashflow_consistency", "Cash-flow consistency", st, f"weekly variation {cv:.2f}, trend {trend:+.1f}%/month", "steady weekly inflows, flat or rising trend",
                     {"good": "Your weekly receipts are fairly steady and not falling.", "watch": "Your receipts vary from week to week or are drifting down.",
                      "gap": "Your receipts swing widely or are falling, which makes repayment capacity hard to judge."}[st]))
    # 2. liabilities burden (declared loans in the submission count as well as repayments seen in the statements)
    inflow = float(f.get("avg_monthly_inflow", 0))
    declared_emi = sum(float(l.get("monthly_emi") or 0) for l in (submission or {}).get("existing_loans", []))
    debt = max(float(f.get("monthly_debt_service", 0)), declared_emi)
    ratio = debt / inflow if inflow > 0 else 1.0
    st = "good" if ratio <= 0.15 else "watch" if ratio <= 0.30 else "gap"
    inds.append(_ind("liabilities_burden", "Existing loan burden", st, f"{_pct(ratio)} of monthly inflow goes to instalments", "15% or less (illustrative)",
                     {"good": "Instalments take a small share of your monthly income.", "watch": "A noticeable part of your income already goes to instalments.",
                      "gap": "A large part of your income already goes to instalments, leaving little room for more debt."}[st]))
    # 3. repayment regularity
    late, bounced = int(f.get("repayment_delay_count_6m", 0)), int(f.get("bounce_count_90d", 0))
    st = "good" if late == 0 and bounced == 0 else "watch" if late + bounced <= 2 else "gap"
    inds.append(_ind("repayment_regularity", "Repayment regularity", st, f"{late} late instalment(s) in 6 months, {bounced} returned payment(s) in 90 days", "none late or returned",
                     {"good": "No late instalments or returned payments in the period reviewed.", "watch": "A small number of late or returned payments appear in your statements.",
                      "gap": "Several late instalments or returned payments appear in your statements."}[st]))
    # 4. working-capital buffer (+ the cash-flow outlook)
    cover = float(f.get("liquidity_cover_days", 0))
    sm = (forecast or {}).get("summary") or {}
    dts = sm.get("days_to_shortfall")
    st = "good" if cover >= 30 and dts is None else "watch" if cover >= 15 and (dts is None or dts > 30) else "gap"
    extra = f"; a cash shortfall is projected in about {dts} days" if dts is not None else ""
    inds.append(_ind("working_capital_buffer", "Working-capital buffer", st, f"about {min(cover, 365):.0f} days of operating cash{extra}", "30 days or more with no projected shortfall",
                     {"good": "You hold roughly a month or more of operating cash and no shortfall is projected.", "watch": "Your cash buffer is modest or a shortfall may be approaching.",
                      "gap": "Your cash buffer is thin or a near-term shortfall is projected."}[st]))
    # 5. revenue diversification
    top = float(f.get("top_customer_share", 0))
    st = "good" if top <= 0.35 else "watch" if top <= 0.55 else "gap"
    inds.append(_ind("revenue_diversification", "Customer diversification", st, f"largest customer is {_pct(top)} of inflows", "35% or less (illustrative)",
                     {"good": "Your income is spread across customers.", "watch": "One customer accounts for a large share of your income.",
                      "gap": "Your income depends heavily on one customer."}[st]))
    # 6. documentation
    d = docs or {}
    req, have = int(d.get("required_count", 0)), int(d.get("present_count", 0))
    frac = have / req if req else 0.0
    has_sub = bool(submission)
    fresh = bool(submission and submission.get("age_days") is not None and submission["age_days"] <= 90)
    st = "good" if frac >= 1 and has_sub and fresh else "gap" if (frac < 0.5 or not has_sub) else "watch"
    inds.append(_ind("documentation_completeness", "Documents and submitted information", st, f"{have} of {req} KYC documents; financial submission {'current' if fresh else 'out of date' if has_sub else 'not provided'}",
                     "all documents and a submission from the last 90 days",
                     {"good": "Your documents are complete and your submitted figures are recent.", "watch": "Some documents are missing or your submitted figures are getting old.",
                      "gap": "Key documents or your financial submission are missing."}[st]))
    # 7. records consistency
    observed = float(f.get("observed_annual_turnover", 0))
    decl = float((submission or {}).get("financials", {}).get("annual_revenue") or declared_turnover or 0)
    r = decl / observed if observed > 0 and decl > 0 else None
    st = "watch" if r is None else "good" if 0.75 <= r <= 1.3 else "watch" if 0.6 <= r <= 1.6 else "gap"
    inds.append(_ind("financial_records_consistency", "Declared vs bank-visible turnover", st, "not enough information to compare" if r is None else f"declared is {r:.2f}x what your account shows", "between 0.75x and 1.3x",
                     {"good": "The turnover you declare matches what your account shows.", "watch": "The comparison is incomplete or the two figures differ somewhat.",
                      "gap": "The turnover you declare differs a lot from what your account shows."}[st]))

    score = {"good": 1.0, "watch": 0.5, "gap": 0.0}
    index = round(sum(i["weight"] * score[i["status"]] for i in inds))
    thin = f.get("data_days", 0) < 45 or f.get("n_txns", 0) < 20
    status = "more_information_needed" if thin else "likely_ready" if index >= 75 else "nearly_ready" if index >= 50 else "needs_improvement"
    strengths = [{"code": i["code"], "label": i["label"], "detail": i["explanation"]} for i in inds if i["status"] == "good"]
    gaps = [{"code": i["code"], "label": i["label"], "severity": i["status"], "detail": i["explanation"]} for i in sorted(inds, key=lambda x: ({"gap": 0, "watch": 1, "good": 2}[x["status"]], -x["weight"])) if i["status"] != "good"]
    return {"status": status, "status_label": STATUS_LABEL[status], "readiness_index": index, "indicators": inds, "strengths": strengths, "gaps": gaps, "criteria_version": CRITERIA_VERSION,
            "as_of": as_of, "limited_data": thin, "disclaimer": DISCLAIMER}


def build_plan(indicators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for i in indicators:
        if i["status"] == "good" or i["code"] not in PLAN_CATALOGUE:
            continue
        c = PLAN_CATALOGUE[i["code"]]
        items.append({"code": i["code"], "title": c["title"], "why": f"{c['why']} (Now: {i['value']}.)", "how": c["how"], "priority": (1 if i["status"] == "gap" else 2) * 100 - i["weight"], "severity": i["status"]})
    return sorted(items, key=lambda x: x["priority"])


def narrative(name: str, r: dict[str, Any]) -> str:
    if r["status"] == "more_information_needed":
        return f"There is not yet enough transaction history for {name} to give a meaningful readiness view. Add more statements or check back after more activity. {DISCLAIMER}"
    s = ", ".join(x["label"].lower() for x in r["strengths"][:3]) or "no clear strengths yet"
    g = "; ".join(f"{x['label'].lower()} ({x['detail'].rstrip('.').lower()})" for x in r["gaps"][:3]) or "no gaps identified"
    return (f"{r['status_label']}: readiness index {r['readiness_index']}/100 on illustrative criteria. Strengths: {s}. Areas to work on: {g}. {DISCLAIMER}")
