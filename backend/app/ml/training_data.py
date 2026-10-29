"""Synthetic labelled data used to train the demo credit and propensity models.

IMPORTANT: labels are produced by a hand-written data-generating process. A model trained on them
learns that process, so any metric computed on this data says NOTHING about real-world performance.
The UI labels every such metric as "synthetic dataset".
"""
from __future__ import annotations

import numpy as np

CREDIT_FEATURES = [
    "log_avg_monthly_inflow", "inflow_cv", "inflow_trend_pct", "net_margin", "bounce_count_90d", "utilization",
    "utilization_change_90d", "bureau_score", "repayment_delay_count_6m", "top_customer_share", "dscr",
    "liquidity_cover_days", "business_age_years", "requested_to_inflow", "avg_receipt_delay_30d",
]

PROPENSITY_FEATURES = [
    "log_avg_monthly_inflow", "inflow_trend_pct", "inflow_cv", "liquidity_cover_days", "avg_receipt_delay_30d", "export_share",
    "utilization", "employees_log", "business_age_years", "txn_count_30d", "avg_balance_ratio", "net_margin", "has_term_loan",
    "has_overdraft", "has_wc_loan", "has_trade_finance", "has_premium_account", "has_insurance",
]

PRODUCTS = ["working_capital_loan", "term_loan", "overdraft", "trade_finance", "current_account_upgrade", "insurance"]
PRODUCT_LABELS = {
    "working_capital_loan": "Working capital loan", "term_loan": "Term loan", "overdraft": "Overdraft",
    "trade_finance": "Trade finance", "current_account_upgrade": "Current account upgrade", "insurance": "Insurance",
}
# products_held strings (as stored on the SME) -> propensity flag
HELD_MAP = {
    "has_term_loan": ("Term loan",), "has_overdraft": ("Overdraft", "Cash credit"), "has_wc_loan": ("Working capital loan",),
    "has_trade_finance": ("Trade finance",), "has_premium_account": ("Premium current account",), "has_insurance": ("Insurance",),
}


def sample_sme_features(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Population of plausible SME feature vectors (correlated through a latent 'stress' factor)."""
    q = rng.normal(0, 1, n)
    f: dict[str, np.ndarray] = {}
    f["log_avg_monthly_inflow"] = rng.normal(np.log(9_000_000), 0.95, n)
    f["inflow_cv"] = np.clip(0.28 + 0.07 * q + np.abs(rng.normal(0, 0.12, n)), 0.05, 1.4)
    f["inflow_trend_pct"] = np.clip(rng.normal(1.2 - 1.8 * q, 3.0), -15, 15)
    f["net_margin"] = np.clip(rng.normal(0.17 - 0.05 * q, 0.07), -0.3, 0.45)
    f["bounce_count_90d"] = rng.poisson(np.clip(0.45 + 0.9 * q, 0.04, 6)).astype(float)
    f["utilization"] = np.clip(0.45 + 0.17 * q + rng.normal(0, 0.12, n), 0.0, 1.0)
    f["utilization_change_90d"] = np.clip(0.03 * q + rng.normal(0, 0.06, n), -0.25, 0.4)
    f["bureau_score"] = np.clip(700 - 45 * q + rng.normal(0, 40, n), 450, 880)
    f["repayment_delay_count_6m"] = rng.poisson(np.clip(0.35 + 0.7 * q, 0.02, 5)).astype(float)
    f["top_customer_share"] = np.clip(0.25 + 0.06 * q + np.abs(rng.normal(0, 0.12, n)), 0.08, 0.95)
    has_debt = rng.random(n) < 0.7
    f["dscr"] = np.where(has_debt, np.clip(rng.normal(2.6 - 0.7 * q, 1.0), -1, 6), 6.0)
    f["liquidity_cover_days"] = np.clip(np.exp(rng.normal(np.log(24) - 0.25 * q, 0.6)), 0, 200)
    f["business_age_years"] = np.clip(np.exp(rng.normal(np.log(7), 0.6, n)), 0.5, 40)
    f["requested_to_inflow"] = np.clip(rng.normal(2.5, 1.4, n), 0, 9)
    f["avg_receipt_delay_30d"] = np.clip(rng.normal(18 + 5 * q, 7), 0, 70)
    # extras only needed by the propensity generator
    f["export_share"] = np.clip(np.where(rng.random(n) < 0.22, rng.beta(2, 3, n), 0.0), 0, 1)
    f["employees_log"] = np.clip(rng.normal(np.log(40), 0.8, n), np.log(3), np.log(400))
    f["txn_count_30d"] = np.clip(rng.normal(55, 25, n), 5, 220)
    f["avg_balance_ratio"] = np.clip(rng.lognormal(np.log(0.22), 0.6, n), 0.01, 2.0)
    for k in ("has_term_loan", "has_overdraft", "has_wc_loan", "has_trade_finance", "has_premium_account", "has_insurance"):
        f[k] = (rng.random(n) < {"has_term_loan": 0.3, "has_overdraft": 0.35, "has_wc_loan": 0.15, "has_trade_finance": 0.06, "has_premium_account": 0.12, "has_insurance": 0.2}[k]).astype(float)
    return f


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


RISK_SCALE = 0.7  # softens extreme combinations so synthetic PDs stay in a plausible range


def credit_logit(f: dict[str, np.ndarray], noise: np.ndarray | float = 0.0, intercept: float = 0.0):
    return RISK_SCALE * (
        intercept
        - 0.012 * (f["bureau_score"] - 700)
        + 0.38 * np.minimum(f["bounce_count_90d"], 6)
        + 2.2 * (f["utilization"] - 0.5)
        + 3.2 * np.maximum(f["utilization_change_90d"], 0)
        + 0.30 * np.minimum(f["repayment_delay_count_6m"], 6)
        - 0.07 * np.clip(f["inflow_trend_pct"], -10, 10)
        + 1.3 * (f["inflow_cv"] - 0.4)
        - 3.0 * (f["net_margin"] - 0.15)
        + 1.6 * np.maximum(f["top_customer_share"] - 0.4, 0)
        - 0.55 * (np.minimum(f["dscr"], 4) - 1.5)
        - 0.014 * np.minimum(f["liquidity_cover_days"], 60)
        - 0.05 * np.minimum(f["business_age_years"], 15)
        + 0.22 * (f["requested_to_inflow"] - 3)
        + 0.016 * f["avg_receipt_delay_30d"]
    ) + noise


def make_credit_dataset(n: int, seed: int, target_rate: float = 0.08):
    rng = np.random.default_rng(seed)
    f = sample_sme_features(n, rng)
    noise = rng.normal(0, 0.7, n)
    lo, hi = -8.0, 8.0
    for _ in range(40):  # bisection for the intercept that yields the target default rate
        mid = (lo + hi) / 2
        rate = _sigmoid(credit_logit(f, noise, mid)).mean()
        lo, hi = (mid, hi) if rate < target_rate else (lo, mid)
    p = _sigmoid(credit_logit(f, noise, (lo + hi) / 2))
    y = (rng.random(n) < p).astype(int)
    X = np.column_stack([f[k] for k in CREDIT_FEATURES])
    return X, y, f


def propensity_logits(f: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    inflow = np.exp(f["log_avg_monthly_inflow"]) / 1e6
    return {
        "working_capital_loan": -2.0 + 0.055 * np.minimum(f["avg_receipt_delay_30d"], 45) + 0.045 * np.clip(f["inflow_trend_pct"], -5, 10) - 0.04 * np.minimum(f["liquidity_cover_days"], 40)
        + 0.9 * (1 - f["has_wc_loan"]) - 1.4 * f["has_wc_loan"] + 0.5 * np.minimum(inflow, 8) / 8,
        "term_loan": -2.3 + 0.12 * np.minimum(f["business_age_years"], 12) + 0.7 * (f["net_margin"] > 0.08) - 1.5 * f["has_term_loan"] + 0.35 * np.minimum(inflow, 10) / 5 - 0.8 * (f["inflow_trend_pct"] < -2),
        "overdraft": -1.8 + 1.5 * np.maximum(f["inflow_cv"] - 0.35, 0) - 0.05 * np.minimum(f["liquidity_cover_days"], 30) - 1.6 * f["has_overdraft"] + 0.5 * (f["avg_balance_ratio"] < 0.12),
        "trade_finance": -3.4 + 4.0 * f["export_share"] - 1.6 * f["has_trade_finance"] + 0.25 * np.minimum(inflow, 8) / 4,
        "current_account_upgrade": -2.6 + 0.02 * np.minimum(f["txn_count_30d"], 120) + 2.2 * np.minimum(f["avg_balance_ratio"], 0.8) - 2.0 * f["has_premium_account"] + 0.3 * np.minimum(inflow, 10) / 5,
        "insurance": -2.4 + 0.9 * (f["employees_log"] - np.log(25)) + 0.04 * np.minimum(f["business_age_years"], 15) - 1.8 * f["has_insurance"] + 0.4 * (f["has_term_loan"] > 0),
    }


def make_propensity_dataset(n: int, seed: int):
    rng = np.random.default_rng(seed)
    f = sample_sme_features(n, rng)
    logits = propensity_logits(f)
    X = np.column_stack([f[k] for k in PROPENSITY_FEATURES])
    Y = np.column_stack([(rng.random(n) < _sigmoid(logits[p] + rng.normal(0, 0.6, n))).astype(int) for p in PRODUCTS])
    return X, Y
