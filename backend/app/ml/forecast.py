"""Cash-flow forecasting: a transparent baseline.

Model (no black box):
  1. Detect recurring obligations (payroll, rent, GST, EMI, utilities, drawings, quarterly advance
     tax) from history -> expected amount (median of the last 3) and day-of-month.
  2. Variable flows (customer receipts, supplier payments, misc opex) use an exponentially weighted
     weekday profile of the last 12 weeks (half-life 4 weeks) - a seasonal-naive/EWMA hybrid.
  3. Scenario levers (receipt delay, revenue decline, expense increase, extra financing, one-off
     expense) transform those components, then balances are projected day by day.

The "uncertainty band" is a HEURISTIC +/-1.28 sigma envelope from the dispersion of recent daily
residuals; it is NOT a statistically validated confidence interval.
"""
from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np

MODEL_VERSION = "cashflow-baseline-v1"
LOOKBACK_DAYS = 84
HALF_LIFE_DAYS = 28.0
Z = 1.28
RECEIPT_CATS = {"customer_receipt", "export_receipt"}
RECURRING_CATS = {"payroll", "rent", "utilities", "loan_repayment", "tax", "owner_drawings"}
VARIABLE_OUT_CATS = {"supplier_payment", "other_opex"}

OUT_LABELS = {
    "supplier_payment": "Supplier payments", "other_opex": "Other operating expenses", "payroll": "Payroll", "rent": "Rent",
    "utilities": "Utilities", "loan_repayment": "Loan EMIs", "tax": "GST & taxes", "owner_drawings": "Promoter drawings",
    "one_time_expense": "One-time expense (scenario)",
}


def _bday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _dom_date(year: int, month: int, dom: int) -> date:
    return _bday(date(year, month, min(dom, calendar.monthrange(year, month)[1])))


def detect_recurring(txns: list[dict], as_of: date) -> list[dict]:
    """Find monthly (and quarterly advance-tax) obligations."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in txns:
        if t["direction"] == "out" and not t["is_bounced"] and t["category"] in RECURRING_CATS and 0 <= (as_of - t["txn_date"]).days <= 130:
            groups[(t["category"], t["counterparty"])].append(t)
    items = []
    for (cat, cp), rows in groups.items():
        rows.sort(key=lambda r: r["txn_date"])
        quarterly = "advance tax" in cp.lower()
        if quarterly:
            # use the last two quarter payments
            amt = float(np.mean([r["amount"] for r in rows[-2:]]))
            items.append({"category": cat, "counterparty": cp, "amount": amt, "dom": 15, "months": {3, 6, 9, 12}})
            continue
        # one per month: amounts of the last 3 months
        if len(rows) < 2:
            continue
        amt = statistics.median([r["amount"] for r in rows[-3:]])
        dom = int(statistics.median([r["txn_date"].day for r in rows[-3:]]))
        items.append({"category": cat, "counterparty": cp, "amount": float(amt), "dom": dom, "months": set(range(1, 13))})
    # quarterly advance tax that did not occur in the lookback (e.g. just before a quarter): look further back
    if not any("advance tax" in i["counterparty"].lower() for i in items):
        old = [t for t in txns if "advance tax" in t["counterparty"].lower() and not t["is_bounced"] and 0 <= (as_of - t["txn_date"]).days <= 400]
        if old:
            old.sort(key=lambda r: r["txn_date"])
            items.append({"category": "tax", "counterparty": old[-1]["counterparty"], "amount": float(np.mean([r["amount"] for r in old[-2:]])), "dom": 15, "months": {3, 6, 9, 12}})
    return items


def weekday_profile(txns: list[dict], as_of: date, direction: str, cats: set[str]) -> tuple[list[float], float]:
    """Exponentially-weighted expected amount per weekday + the plain 12-week monthly average (for run-rate change)."""
    daily: dict[date, float] = defaultdict(float)
    for t in txns:
        if t["direction"] == direction and t["category"] in cats and not t["is_bounced"] and 0 <= (as_of - t["txn_date"]).days < LOOKBACK_DAYS:
            daily[t["txn_date"]] += t["amount"]
    num = [0.0] * 7
    den = [0.0] * 7
    for i in range(LOOKBACK_DAYS):
        d = as_of - timedelta(days=i)
        w = 0.5 ** (i / HALF_LIFE_DAYS)
        num[d.weekday()] += w * daily.get(d, 0.0)
        den[d.weekday()] += w
    prof = [num[k] / den[k] if den[k] else 0.0 for k in range(7)]
    plain_monthly = sum(daily.values()) / LOOKBACK_DAYS * 30.4
    return prof, plain_monthly


def _residual_sigma(txns: list[dict], as_of: date, prof_in: list[float], prof_out: list[float]) -> float:
    res = []
    for i in range(56):
        d = as_of - timedelta(days=i)
        act = sum((t["amount"] if t["direction"] == "in" and t["category"] in RECEIPT_CATS else
                   -t["amount"] if t["direction"] == "out" and t["category"] in VARIABLE_OUT_CATS else 0.0)
                  for t in txns if t["txn_date"] == d and not t["is_bounced"])
        res.append(act - (prof_in[d.weekday()] - prof_out[d.weekday()]))
    return float(np.std(res)) if res else 0.0


DEFAULT_SCENARIO = {
    "receipt_delay_days": 0, "revenue_decline_pct": 0.0, "expense_increase_pct": 0.0,
    "additional_financing": 0.0, "financing_day": 1, "one_time_expense": 0.0, "one_time_day": 10,
}


def normalize_scenario(s: dict | None) -> dict:
    out = dict(DEFAULT_SCENARIO)
    for k, v in (s or {}).items():
        if k in out and v is not None:
            out[k] = v
    out["receipt_delay_days"] = int(max(0, min(60, out["receipt_delay_days"])))
    out["revenue_decline_pct"] = float(max(0.0, min(80.0, out["revenue_decline_pct"])))
    out["expense_increase_pct"] = float(max(0.0, min(80.0, out["expense_increase_pct"])))
    out["additional_financing"] = float(max(0.0, out["additional_financing"]))
    out["one_time_expense"] = float(max(0.0, out["one_time_expense"]))
    out["financing_day"] = int(max(1, out["financing_day"]))
    out["one_time_day"] = int(max(1, out["one_time_day"]))
    return out


def forecast_cashflow(txns: list[dict], as_of: date, horizon_days: int = 60, scenario: dict | None = None,
                      facility_headroom: float = 0.0, history_days: int = 90) -> dict[str, Any]:
    horizon_days = int(max(7, min(180, horizon_days)))
    sc = normalize_scenario(scenario)
    is_baseline = sc == normalize_scenario(None)
    txns = [t for t in txns if t["txn_date"] <= as_of]
    if not txns:
        raise ValueError("No transactions available to forecast from.")
    opening = float(sorted(txns, key=lambda t: (t["txn_date"]))[-1]["balance_after"])
    prof_in, plain_in_monthly = weekday_profile(txns, as_of, "in", RECEIPT_CATS)
    prof_out, _ = weekday_profile(txns, as_of, "out", VARIABLE_OUT_CATS)
    recurring = detect_recurring(txns, as_of)
    sigma = _residual_sigma(txns, as_of, prof_in, prof_out)
    ew_in_monthly = sum(prof_in) * (30.4 / 7)
    run_rate_change = (ew_in_monthly - plain_in_monthly) / plain_in_monthly * 100 if plain_in_monthly else 0.0

    rev_factor = 1 - sc["revenue_decline_pct"] / 100
    exp_factor = 1 + sc["expense_increase_pct"] / 100
    sup_factor = (1 - 0.5 * sc["revenue_decline_pct"] / 100) * exp_factor

    base_in = []  # baseline (undelayed) receipts by day
    dates = [as_of + timedelta(days=i + 1) for i in range(horizon_days)]
    for d in dates:
        base_in.append(prof_in[d.weekday()] * rev_factor)
    delay = sc["receipt_delay_days"]
    in_series = [(base_in[i - delay] if i - delay >= 0 else 0.0) for i in range(horizon_days)]

    out_by_cat: dict[str, list[float]] = {k: [0.0] * horizon_days for k in OUT_LABELS}
    recurring_events: list[dict] = []
    for i, d in enumerate(dates):
        sv = prof_out[d.weekday()] * sup_factor
        out_by_cat["supplier_payment"][i] += sv * 0.88
        out_by_cat["other_opex"][i] += sv * 0.12
    for item in recurring:
        for i, d in enumerate(dates):
            if d.month in item["months"]:
                due = _dom_date(d.year, d.month, item["dom"])
                if due == d:
                    amt = item["amount"]
                    if item["category"] == "tax" and "advance" not in item["counterparty"].lower():
                        amt *= rev_factor  # net GST follows revenue
                    elif item["category"] in ("payroll", "rent", "utilities"):
                        amt *= exp_factor
                    out_by_cat[item["category"]][i] += amt
                    recurring_events.append({"date": d.isoformat(), "category": item["category"], "label": item["counterparty"], "amount": round(amt)})
    if sc["one_time_expense"] > 0:
        j = min(sc["one_time_day"], horizon_days) - 1
        out_by_cat["one_time_expense"][j] += sc["one_time_expense"]
    fin = [0.0] * horizon_days
    if sc["additional_financing"] > 0:
        fin[min(sc["financing_day"], horizon_days) - 1] += sc["additional_financing"]

    inflow = [in_series[i] + fin[i] for i in range(horizon_days)]
    outflow = [sum(out_by_cat[c][i] for c in out_by_cat) for i in range(horizon_days)]
    bal, run = [], opening
    for i in range(horizon_days):
        run += inflow[i] - outflow[i]
        bal.append(run)
    lower = [b - Z * sigma * (i + 1) ** 0.5 for i, b in enumerate(bal)]
    upper = [b + Z * sigma * (i + 1) ** 0.5 for i, b in enumerate(bal)]

    # buffer = 7 days of average outflow
    hist_out = sum(t["amount"] for t in txns if t["direction"] == "out" and not t["is_bounced"] and 0 <= (as_of - t["txn_date"]).days < 90)
    avg_daily_out = hist_out / 90
    buffer = round(7 * avg_daily_out)
    min_b = min(bal)
    min_i = bal.index(min_b)
    low_i = next((i for i, b in enumerate(bal) if b < buffer), None)
    short_i = next((i for i, b in enumerate(bal) if b < 0), None)
    shortfall_amount = max(0.0, -min_b)

    # --- history for the chart (daily inflow / outflow / balance)
    hist_start = as_of - timedelta(days=history_days - 1)
    din: dict[date, float] = defaultdict(float)
    dout: dict[date, float] = defaultdict(float)
    for t in txns:
        if t["is_bounced"] or t["txn_date"] < hist_start:
            continue
        (din if t["direction"] == "in" else dout)[t["txn_date"]] += t["amount"]
    from app.ml.features import daily_balance_series

    hb = dict(daily_balance_series(txns, as_of, history_days))
    history = [{"date": (hist_start + timedelta(days=i)).isoformat(), "inflow": round(din.get(hist_start + timedelta(days=i), 0.0)),
                "outflow": round(dout.get(hist_start + timedelta(days=i), 0.0)), "balance": round(hb[hist_start + timedelta(days=i)])} for i in range(history_days)]

    tot_in = sum(inflow)
    out_totals = {OUT_LABELS[c]: round(sum(v)) for c, v in out_by_cat.items() if sum(v) > 0}
    # --- contributing factors (evidence-based)
    factors: list[str] = []
    rec_total = sum(sum(out_by_cat[c]) for c in ("payroll", "rent", "utilities", "loan_repayment", "tax", "owner_drawings"))
    factors.append(f"Scheduled obligations in the next {horizon_days} days total ₹{rec_total / 1e5:.1f} L (payroll, rent, GST/tax, EMIs, drawings).")
    factors.append(f"Expected receipts total ₹{tot_in / 1e5:.1f} L; the recent run-rate is {run_rate_change:+.0f}% versus the 12-week average.")
    if recurring_events:
        big = max(recurring_events, key=lambda e: e["amount"])
        factors.append(f"Largest scheduled outflow: {big['label']} ₹{big['amount'] / 1e5:.1f} L on {big['date']}.")
    if delay:
        factors.append(f"Scenario: customer receipts delayed by {delay} days creates a {delay}-day gap in inflows.")
    if sc["revenue_decline_pct"]:
        factors.append(f"Scenario: receipts reduced by {sc['revenue_decline_pct']:.0f}% (supplier payments assumed to flex by half of that).")
    if sc["expense_increase_pct"]:
        factors.append(f"Scenario: operating expenses increased by {sc['expense_increase_pct']:.0f}%.")
    if sc["additional_financing"]:
        factors.append(f"Scenario: ₹{sc['additional_financing'] / 1e5:.1f} L of additional financing received on day {sc['financing_day']}.")
    if sc["one_time_expense"]:
        factors.append(f"Scenario: one-time expense of ₹{sc['one_time_expense'] / 1e5:.1f} L on day {sc['one_time_day']}.")
    drawings = sum(out_by_cat["owner_drawings"])

    # --- recommendation
    if short_i is not None:
        sd = dates[short_i]
        need = shortfall_amount + buffer
        rec = (f"Projected overdraw from {sd.isoformat()} (−₹{shortfall_amount / 1e5:.1f} L at the low point). Arrange ~₹{need / 1e5:.1f} L of bridge liquidity "
               f"before then (working-capital top-up or invoice financing), accelerate receivables")
        if drawings > 0:
            rec += f" and consider deferring promoter drawings (₹{drawings / 1e5:.1f} L in the period)"
        rec += "."
        if facility_headroom > 0:
            rec += f" Undrawn facility headroom is about ₹{facility_headroom / 1e5:.1f} L."
    elif low_i is not None:
        rec = f"Balance is projected to dip below the 7-day cash buffer (₹{buffer / 1e5:.1f} L) on {dates[low_i].isoformat()}. Monitor receipts and consider a standby overdraft line."
    else:
        rec = "No shortfall projected under these assumptions. Surplus cash could be considered for short-term deposits."

    return {
        "model": MODEL_VERSION,
        "method": "EWMA weekday profile + detected recurring obligations (transparent baseline, not statistically validated)",
        "is_baseline": is_baseline,
        "scenario": sc,
        "as_of": as_of.isoformat(),
        "horizon_days": horizon_days,
        "opening_balance": round(opening),
        "buffer_threshold": buffer,
        "facility_headroom": round(facility_headroom),
        "series": [{"date": d.isoformat(), "inflow": round(inflow[i]), "outflow": round(outflow[i]), "balance": round(bal[i]),
                    "lower": round(lower[i]), "upper": round(upper[i])} for i, d in enumerate(dates)],
        "history": history,
        "summary": {
            "expected_ending_balance": round(bal[-1]),
            "min_balance": round(min_b),
            "min_balance_date": dates[min_i].isoformat(),
            "low_balance_date": dates[low_i].isoformat() if low_i is not None else None,
            "shortfall_date": dates[short_i].isoformat() if short_i is not None else None,
            "days_to_shortfall": (short_i + 1) if short_i is not None else None,
            "days_to_low_balance": (low_i + 1) if low_i is not None else None,
            "shortfall_amount": round(shortfall_amount),
            "total_inflow": round(tot_in),
            "total_outflow": round(sum(outflow)),
        },
        "outflow_by_category": out_totals,
        "inflow_by_category": {"Customer receipts": round(sum(in_series)), **({"Financing (scenario)": round(sum(fin))} if sum(fin) else {})},
        "recurring_events": sorted(recurring_events, key=lambda e: e["date"])[:40],
        "factors": factors,
        "recommended_action": rec,
        "uncertainty": {"sigma_daily": round(sigma), "z": Z,
                        "note": "Heuristic envelope from recent daily residual dispersion (±1.28σ·√t). Not a validated confidence interval."},
        "assumptions": [
            "Recurring obligations repeat at their recent amount and day-of-month; one-off capex and new debt are not forecast.",
            "Variable receipts and supplier payments follow a recency-weighted weekday profile of the last 12 weeks.",
            "Receivable delay scenario shifts all receipts later by N days; revenue decline scales receipts and GST, and supplier payments flex by half.",
            "Overdraft / facility headroom is shown for context only and is not netted against the projected balance.",
        ],
        "run_rate_change_pct": round(run_rate_change, 1),
    }


def backtest(txns: list[dict], as_of: date, holdout_days: int = 30) -> dict | None:
    """Forecast the last ``holdout_days`` from earlier data and compare with what actually happened."""
    cut = as_of - timedelta(days=holdout_days)
    train = [t for t in txns if t["txn_date"] <= cut]
    if len({t["txn_date"] for t in train}) < 60:
        return None
    fc = forecast_cashflow(train, cut, holdout_days, None, 0.0, history_days=30)
    from app.ml.features import daily_balance_series

    actual = dict(daily_balance_series([t for t in txns], as_of, holdout_days))
    errs, pred_min, act_min = [], 1e18, 1e18
    for row in fc["series"]:
        d = date.fromisoformat(row["date"])
        if d in actual:
            errs.append(abs(row["balance"] - actual[d]))
            pred_min = min(pred_min, row["balance"])
            act_min = min(act_min, actual[d])
    out_90 = sum(t["amount"] for t in train if t["direction"] == "out" and not t["is_bounced"] and 0 <= (cut - t["txn_date"]).days < 90)
    avg_monthly_out = out_90 / 90 * 30.4
    buffer = fc["buffer_threshold"]
    return {
        "mae_balance": float(np.mean(errs)) if errs else None,
        "mae_pct_of_monthly_outflow": float(np.mean(errs) / avg_monthly_out * 100) if errs and avg_monthly_out else None,
        "predicted_low": pred_min < buffer, "actual_low": act_min < buffer,
        "predicted_min": pred_min, "actual_min": act_min,
    }
