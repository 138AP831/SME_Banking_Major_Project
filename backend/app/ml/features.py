"""Shared feature builder - ONE definition of the SME financial / behavioural features that the
propensity, credit, forecasting and EWS modules all consume (so the same SME looks the same in
every module)."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np

FEATURE_VERSION = "features-v1"

OPERATING_EXCLUDED = {"loan_repayment", "owner_drawings", "capex"}


def _window(txns: list[dict], as_of: date, start_ago: int, end_ago: int) -> list[dict]:
    """Transactions with as_of-end_ago <= date < as_of-start_ago (non-bounced)."""
    lo, hi = as_of - timedelta(days=end_ago), as_of - timedelta(days=start_ago)
    return [t for t in txns if lo < t["txn_date"] <= hi and not t["is_bounced"]]


def _sum_dir(ts: list[dict], direction: str, cats: set[str] | None = None, exclude: set[str] | None = None) -> float:
    return float(sum(t["amount"] for t in ts if t["direction"] == direction
                     and (cats is None or t["category"] in cats) and (exclude is None or t["category"] not in exclude)))


def daily_balance_series(txns: list[dict], as_of: date, days: int) -> list[tuple[date, float]]:
    """End-of-day balance for each of the last ``days`` days (forward filled)."""
    last: dict[date, float] = {}
    for t in txns:
        if t["txn_date"] <= as_of:
            last[t["txn_date"]] = t["balance_after"]
    first_known = min(last) if last else as_of
    out: list[tuple[date, float]] = []
    prev = None
    # find the balance before the window start
    start = as_of - timedelta(days=days - 1)
    prior = [d for d in last if d < start]
    if prior:
        prev = last[max(prior)]
    for i in range(days):
        d = start + timedelta(days=i)
        if d in last:
            prev = last[d]
        if prev is None:
            prev = last.get(first_known, 0.0)
        out.append((d, float(prev)))
    return out


def build_features(sme: dict, txns: list[dict], as_of: date, request: dict | None = None) -> dict[str, Any]:
    """Compute the shared feature dictionary for one SME. ``sme`` is a plain dict of the SME row."""
    txns = [t for t in txns if t["txn_date"] <= as_of]
    f: dict[str, Any] = {"feature_version": FEATURE_VERSION, "as_of": as_of.isoformat()}
    f["n_txns"] = len(txns)
    first = min((t["txn_date"] for t in txns), default=as_of)
    f["data_days"] = (as_of - first).days
    # --- monthly windows (0 = most recent 30 days)
    m_in, m_out = [], []
    for i in range(6):
        w = _window(txns, as_of, i * 30, (i + 1) * 30)
        m_in.append(_sum_dir(w, "in"))
        m_out.append(_sum_dir(w, "out"))
    f["monthly_inflows"] = [round(x) for x in m_in]
    f["monthly_outflows"] = [round(x) for x in m_out]
    n_months = max(1, min(3, f["data_days"] // 30))
    avg_in = sum(m_in[:n_months]) / n_months
    avg_out = sum(m_out[:n_months]) / n_months
    f["avg_monthly_inflow"] = round(avg_in, 2)
    f["avg_monthly_outflow"] = round(avg_out, 2)
    f["log_avg_monthly_inflow"] = round(math.log(max(avg_in, 1.0)), 4)
    f["net_cash_margin"] = round((avg_in - avg_out) / avg_in, 4) if avg_in > 0 else 0.0  # after EMI, drawings, capex, tax
    # --- trend of inflows (percent of mean per month; positive = growing)
    usable = [x for x in m_in[: max(2, min(6, f["data_days"] // 30))]]
    if len(usable) >= 3 and np.mean(usable) > 0:
        xs = np.arange(len(usable))[::-1]  # oldest has the most negative index
        slope = np.polyfit(xs, np.array(usable), 1)[0]
        f["inflow_trend_pct"] = round(float(np.clip(slope / np.mean(usable) * 100, -25, 25)), 3)
    else:
        f["inflow_trend_pct"] = 0.0
    prev_avg = (sum(m_in[1:3]) / 2) if f["data_days"] >= 60 else avg_in
    f["inflow_last30_vs_prev60_pct"] = round((m_in[0] - prev_avg) / prev_avg * 100, 2) if prev_avg > 0 else 0.0
    # --- weekly inflow volatility
    weekly = [_sum_dir(_window(txns, as_of, i * 7, (i + 1) * 7), "in") for i in range(13)]
    weekly = weekly[: max(4, min(13, f["data_days"] // 7))]
    f["inflow_cv"] = round(float(np.std(weekly) / np.mean(weekly)), 4) if weekly and np.mean(weekly) > 0 else 0.0
    # --- balances
    bal = daily_balance_series(txns, as_of, 91)
    cur = bal[-1][1] if bal else float(sme.get("current_balance", 0.0))
    f["current_balance"] = round(cur, 2)
    f["avg_balance_30d"] = round(float(np.mean([b for _, b in bal[-30:]])), 2) if bal else 0.0
    f["min_balance_90d"] = round(float(min(b for _, b in bal)), 2) if bal else 0.0
    b30 = bal[-31][1] if len(bal) >= 31 else bal[0][1] if bal else 0.0
    f["balance_30d_ago"] = round(b30, 2)
    f["balance_change_30d_pct"] = round((cur - b30) / max(abs(b30), 1.0), 4)
    f["liquidity_cover_days"] = round(cur / (avg_out / 30.4), 2) if avg_out > 0 else 999.0
    f["avg_balance_ratio"] = round(f["avg_balance_30d"] / max(avg_in, 1.0), 4)
    # --- bounced payments (cheque / NACH returns)
    b90 = [t for t in txns if t["is_bounced"] and (as_of - t["txn_date"]).days <= 90]
    attempts = len([t for t in txns if t["direction"] == "out" and (as_of - t["txn_date"]).days <= 90])
    f["bounce_count_90d"] = len(b90)
    f["bounce_rate_90d"] = round(len(b90) / attempts, 4) if attempts else 0.0
    # --- concentration (last 180 days of inflows)
    cp: dict[str, float] = defaultdict(float)
    for t in _window(txns, as_of, 0, 180):
        if t["direction"] == "in":
            cp[t["counterparty"]] += t["amount"]
    tot = sum(cp.values())
    top_cp = max(cp, key=cp.get) if cp else None
    # consumer / POS settlements are inherently diversified - not a single-customer concentration
    f["top_customer_share"] = 0.05 if (top_cp and top_cp.upper().startswith("POS")) else (round(cp[top_cp] / tot, 4) if tot else 0.0)
    f["n_customers"] = len(cp)
    f["top_customer"] = top_cp
    exp = sum(t["amount"] for t in _window(txns, as_of, 0, 180) if t["category"] == "export_receipt")
    f["export_share"] = round(exp / tot, 4) if tot else 0.0
    # --- activity
    c30 = len(_window(txns, as_of, 0, 30))
    c_prev = len(_window(txns, as_of, 30, 90)) / 2 if f["data_days"] >= 60 else c30
    f["txn_count_30d"] = c30
    f["activity_change_pct"] = round((c30 - c_prev) / c_prev, 4) if c_prev else 0.0
    # --- receipt delays
    r30 = [t["days_delay"] for t in _window(txns, as_of, 0, 30) if t["category"] in ("customer_receipt", "export_receipt")]
    rprev = [t["days_delay"] for t in _window(txns, as_of, 30, 90) if t["category"] in ("customer_receipt", "export_receipt")]
    f["avg_receipt_delay_30d"] = round(float(np.mean(r30)), 2) if r30 else 0.0
    f["avg_receipt_delay_prev"] = round(float(np.mean(rprev)), 2) if rprev else f["avg_receipt_delay_30d"]
    f["receipt_delay_change"] = round(f["avg_receipt_delay_30d"] - f["avg_receipt_delay_prev"], 2)
    # --- repayments
    reps = [t for t in txns if t["category"] == "loan_repayment" and not t["is_bounced"] and (as_of - t["txn_date"]).days <= 180]
    f["repayment_delay_count_6m"] = sum(1 for t in reps if t["days_delay"] > 3)
    f["max_repayment_delay"] = max((t["days_delay"] for t in reps), default=0)
    f["repayment_delays_recent"] = [t["days_delay"] for t in sorted(reps, key=lambda t: t["txn_date"])][-6:]
    emi = _sum_dir(_window(txns, as_of, 0, 90), "out", {"loan_repayment"}) / 3
    f["monthly_debt_service"] = round(emi, 2)
    op_out = _sum_dir(_window(txns, as_of, 0, 90), "out", exclude=OPERATING_EXCLUDED) / 3
    cads = avg_in - op_out
    f["net_margin"] = round(cads / avg_in, 4) if avg_in > 0 else 0.0  # operating cash margin (before debt service, drawings, capex)
    f["dscr"] = round(float(np.clip(cads / emi, -2, 6.0)), 3) if emi > 0 else 6.0
    # --- facility
    f["utilization"] = float(sme.get("utilization") or 0.0)
    uh = sme.get("utilization_history") or []
    f["utilization_change_90d"] = round(uh[-1] - uh[-4], 4) if len(uh) >= 4 else 0.0
    f["has_facility_history"] = bool(uh)
    f["outstanding_exposure"] = float(sme.get("outstanding_exposure") or 0.0)
    f["sanctioned_limit"] = float(sme.get("sanctioned_limit") or 0.0)
    # --- profile
    bureau = sme.get("bureau_score")
    f["bureau_score_missing"] = bureau is None
    f["bureau_score"] = int(bureau) if bureau is not None else 650
    inc = sme.get("incorporation_date")
    f["business_age_years"] = round((as_of - inc).days / 365.25, 2) if inc else 3.0
    f["employees"] = int(sme.get("employees") or 0)
    obs_annual = avg_in * 12
    f["observed_annual_turnover"] = round(obs_annual, 2)
    decl = float(sme.get("declared_annual_turnover") or 0.0)
    f["declared_to_observed_turnover"] = round(decl / obs_annual, 3) if obs_annual > 0 else 0.0
    # --- request
    req = request or {}
    f["requested_amount"] = float(req.get("requested_amount", 0.0))
    f["tenure_months"] = int(req.get("tenure_months", 24))
    f["requested_to_inflow"] = round(f["requested_amount"] / avg_in, 3) if avg_in > 0 else 0.0
    f["products_held"] = list(sme.get("products_held") or [])
    return f


def feature_quality(f: dict[str, Any]) -> dict[str, Any]:
    """Light data-quality checks used by the ``validate_data`` node and the model monitor."""
    issues = []
    if f["data_days"] < 90:
        issues.append({"code": "THIN_HISTORY", "severity": "blocking" if f["data_days"] < 45 else "warning",
                       "message": f"Only {f['data_days']} days of transaction history (>= 90 recommended)."})
    if f["n_txns"] < 40:
        issues.append({"code": "FEW_TRANSACTIONS", "severity": "blocking" if f["n_txns"] < 20 else "warning", "message": f"Only {f['n_txns']} transactions available."})
    if f["bureau_score_missing"]:
        issues.append({"code": "BUREAU_MISSING", "severity": "warning", "message": "No bureau score on file - a neutral value is used by the model and the case is referred by policy."})
    if f["declared_to_observed_turnover"] and (f["declared_to_observed_turnover"] > 1.35 or f["declared_to_observed_turnover"] < 0.65):
        issues.append({"code": "TURNOVER_CONFLICT", "severity": "warning",
                       "message": f"Declared turnover is {f['declared_to_observed_turnover']:.2f}x the bank-visible annualised inflows (conflicting financial data)."})
    return {"issues": issues, "blocking": any(i["severity"] == "blocking" for i in issues)}
