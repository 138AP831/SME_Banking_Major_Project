"""Prospecting & propensity.

A one-vs-rest logistic regression trained on SYNTHETIC adoption labels produces a per-product
*propensity score* (0-100). It is a ranking score - NOT a statistically calibrated probability and it
says nothing about real-world conversion. Supporting indicators are transparent rules over the
real shared features so every recommendation is explainable.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.config import get_settings
from app.ml.training_data import HELD_MAP, PRODUCT_LABELS, PRODUCTS, PROPENSITY_FEATURES, make_propensity_dataset

MODEL_VERSION = "propensity-logreg-synthetic-v1"
_lock = threading.Lock()
_bundle: dict[str, Any] | None = None


def _path() -> Path:
    return Path(get_settings().models_dir) / f"{MODEL_VERSION}.joblib"


def train(seed: int = 11) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    X, Y = make_propensity_dataset(9000, seed)
    X_tr, X_te, Y_tr, Y_te = train_test_split(X, Y, test_size=0.25, random_state=seed)
    model = make_pipeline(StandardScaler(), OneVsRestClassifier(LogisticRegression(C=1.0, max_iter=600)))
    model.fit(X_tr, Y_tr)
    P = model.predict_proba(X_te)
    aucs, lifts = {}, {}
    for j, p in enumerate(PRODUCTS):
        aucs[p] = float(roc_auc_score(Y_te[:, j], P[:, j]))
        k = max(1, int(0.1 * len(P)))
        top = np.argsort(-P[:, j])[:k]
        base = Y_te[:, j].mean()
        lifts[p] = float(Y_te[top, j].mean() / base) if base else None
    bundle = {"model": model, "version": MODEL_VERSION, "features": PROPENSITY_FEATURES,
              "metrics": {"auc_by_product": aucs, "macro_auc": float(np.mean(list(aucs.values()))), "top_decile_lift": lifts,
                          "base_rate": {p: float(Y[:, j].mean()) for j, p in enumerate(PRODUCTS)}, "n_train": int(len(X_tr)), "n_test": int(len(X_te))}}
    joblib.dump(bundle, _path())
    return bundle


def load() -> dict[str, Any] | None:
    global _bundle
    with _lock:
        if _bundle is None:
            try:
                _bundle = joblib.load(_path()) if _path().exists() else train()
            except Exception:  # noqa: BLE001
                _bundle = None
        return _bundle


def reset_cache() -> None:
    global _bundle
    with _lock:
        _bundle = None


def _vector(f: dict[str, Any], sme: dict) -> np.ndarray:
    held = set(sme.get("products_held") or [])
    row = {
        "log_avg_monthly_inflow": f["log_avg_monthly_inflow"], "inflow_trend_pct": f["inflow_trend_pct"], "inflow_cv": f["inflow_cv"],
        "liquidity_cover_days": min(f["liquidity_cover_days"], 200), "avg_receipt_delay_30d": f["avg_receipt_delay_30d"], "export_share": f["export_share"],
        "utilization": f["utilization"], "employees_log": math.log(max(3, f["employees"])), "business_age_years": f["business_age_years"],
        "txn_count_30d": f["txn_count_30d"], "avg_balance_ratio": min(f["avg_balance_ratio"], 2.0), "net_margin": f["net_margin"],
    }
    for flag, names in HELD_MAP.items():
        row[flag] = 1.0 if any(n in held for n in names) else 0.0
    return np.array([[float(row[k]) for k in PROPENSITY_FEATURES]])


def _round50k(x: float) -> float:
    return float(round(x / 50_000) * 50_000)


def _indicators(prod: str, f: dict[str, Any]) -> list[str]:
    ind: list[str] = []
    if prod == "working_capital_loan":
        if f["avg_receipt_delay_30d"] >= 18:
            ind.append(f"Customers pay ~{f['avg_receipt_delay_30d']:.0f} days late, stretching the working-capital cycle")
        if f["liquidity_cover_days"] < 20:
            ind.append(f"Only {f['liquidity_cover_days']:.0f} day(s) of cash cover")
        if f["inflow_trend_pct"] >= 2:
            ind.append(f"Receipts growing ~{f['inflow_trend_pct']:.1f}% a month - growth needs funding")
    elif prod == "term_loan":
        if f["net_margin"] >= 0.12:
            ind.append(f"Healthy operating cash margin of {f['net_margin']:.0%}")
        if f["business_age_years"] >= 4:
            ind.append(f"Established business ({f['business_age_years']:.0f} years)")
        if f["dscr"] >= 2:
            ind.append(f"Comfortable debt-service cover ({min(f['dscr'], 6):.1f}x)")
    elif prod == "overdraft":
        if f["inflow_cv"] >= 0.35:
            ind.append(f"Volatile weekly receipts (CV {f['inflow_cv']:.2f}) - a standby line smooths timing gaps")
        if f["avg_balance_ratio"] < 0.15:
            ind.append("Low average balance relative to monthly receipts")
    elif prod == "trade_finance":
        if f["export_share"] >= 0.1:
            ind.append(f"{f['export_share']:.0%} of receipts are export remittances")
    elif prod == "current_account_upgrade":
        if f["txn_count_30d"] >= 50:
            ind.append(f"{f['txn_count_30d']} transactions in 30 days - a high-volume account")
        if f["avg_balance_ratio"] >= 0.2:
            ind.append("Healthy average balances relative to receipts")
    elif prod == "insurance":
        if f["employees"] >= 30:
            ind.append(f"{f['employees']} employees - group health/accident cover relevant")
        if f["business_age_years"] >= 5:
            ind.append("Established asset base worth protecting")
    return ind[:3] or ["Profile resembles customers who adopted this product in the synthetic training data"]


def _size(prod: str, f: dict[str, Any], sme: dict) -> float:
    m = f["avg_monthly_inflow"]
    if prod == "working_capital_loan":
        return _round50k(m * 1.0 * min(1.5, max(0.5, f["avg_receipt_delay_30d"] / 30)))
    if prod == "term_loan":
        return _round50k(m * 2.0)
    if prod == "overdraft":
        return _round50k(m * 0.5)
    if prod == "trade_finance":
        return _round50k(m * max(f["export_share"], 0.05) * 2.0)
    if prod == "current_account_upgrade":
        return 60_000.0  # indicative annual fee income
    return float(f["employees"] * 9_000)  # indicative annual premium


OUTREACH = {
    "working_capital_loan": "Offer a working-capital line sized around {size}; RM call this week.",
    "term_loan": "Discuss a capex/term loan of about {size}; share an indicative term sheet.",
    "overdraft": "Propose a standby overdraft of about {size} to smooth receipt timing.",
    "trade_finance": "Introduce export bill discounting / trade finance up to {size}.",
    "current_account_upgrade": "Offer the premium current-account tier (~{size} a year fee income).",
    "insurance": "Offer group health/accident cover (~{size} indicative annual premium).",
}


def recommend(f: dict[str, Any], sme: dict) -> dict[str, Any]:
    bundle = load()
    held = set(sme.get("products_held") or [])
    scores: dict[str, float]
    if bundle is not None:
        proba = bundle["model"].predict_proba(_vector(f, sme))[0]
        scores = {p: round(float(proba[j]) * 100, 1) for j, p in enumerate(PRODUCTS)}
        method, version = "one-vs-rest logistic regression (synthetic labels)", bundle["version"]
    else:  # transparent fallback so the module never hard-fails
        scores = {p: 30.0 for p in PRODUCTS}
        scores["working_capital_loan"] += min(30, f["avg_receipt_delay_30d"])
        scores["trade_finance"] += 80 * f["export_share"]
        scores["overdraft"] += 40 * f["inflow_cv"]
        method, version = "rule-based fallback (model unavailable)", "propensity-rules-v1"
    held_prod = set()
    for flag, names in HELD_MAP.items():
        if any(n in held for n in names):
            held_prod.add({"has_term_loan": "term_loan", "has_overdraft": "overdraft", "has_wc_loan": "working_capital_loan", "has_trade_finance": "trade_finance",
                           "has_premium_account": "current_account_upgrade", "has_insurance": "insurance"}[flag])
    candidates = {p: s for p, s in scores.items() if p not in held_prod}
    best = max(candidates, key=candidates.get)
    size = _size(best, f, sme)
    consent = sme.get("consent_status", "granted")
    allowed = consent == "granted"
    size_txt = f"₹{size / 1e5:.1f} L" if best not in ("current_account_upgrade", "insurance") else f"₹{size / 1e3:.0f}k"
    action = OUTREACH[best].format(size=size_txt) if allowed else f"Outreach blocked: marketing/data-use consent is '{consent}'. Obtain consent first."
    ind = _indicators(best, f)
    return {
        "scores": scores, "held_products": sorted(held_prod), "product": best, "product_label": PRODUCT_LABELS[best], "propensity_score": candidates[best],
        "est_value": size, "est_value_basis": "indicative facility size" if best in ("working_capital_loan", "term_loan", "overdraft", "trade_finance") else "indicative annual revenue to bank",
        "indicators": ind, "outreach_action": action, "outreach_allowed": allowed, "consent_status": consent, "method": method, "model_version": version,
        "rationale": (f"{PRODUCT_LABELS[best]} has the highest propensity score ({candidates[best]:.0f}/100) among products not already held. "
                      f"Key indicators: {'; '.join(ind)}. This is an uncalibrated ranking score from a model trained on synthetic data - not a probability of conversion."),
    }
