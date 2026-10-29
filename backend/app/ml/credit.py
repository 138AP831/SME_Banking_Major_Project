"""Credit scoring & explainability.

* Estimated PD: XGBoost trained on SYNTHETIC labelled data (see training_data.py); metrics are
  computed on a held-out synthetic split and are NOT evidence of real-world accuracy.
* Model score: a monotone transform of the PD onto a 300-900 scale (score = offset + factor*ln(odds)),
  the standard scorecard scaling, so the two never contradict each other.
* Explanations: real TreeSHAP contributions (shap.TreeExplainer, falling back to XGBoost's native
  pred_contribs - the same algorithm) translated to plain language.
* Fallback: a deterministic points scorecard (no PD) when the model is unavailable. Rule-based
  explanations are labelled as such.
Policy outcomes live in app/services/policy.py (kept separate on purpose).
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.config import get_settings
from app.ml.training_data import CREDIT_FEATURES, make_credit_dataset

MODEL_VERSION = "credit-xgb-synthetic-v1"
PDO, BASE_SCORE, BASE_ODDS = 50.0, 700.0, 30.0
_FACTOR = PDO / math.log(2)
_OFFSET = BASE_SCORE - _FACTOR * math.log(BASE_ODDS)

RISK_BANDS_PD = [(0.03, "Low"), (0.08, "Moderate"), (0.18, "Elevated")]
_lock = threading.Lock()
_bundle: dict[str, Any] | None = None
_explainer: Any = None


def pd_to_score(pd_: float) -> int:
    pd_ = min(max(pd_, 1e-4), 0.9999)
    return int(round(min(900, max(300, _OFFSET + _FACTOR * math.log((1 - pd_) / pd_)))))


def risk_category_from_pd(pd_: float) -> str:
    for thr, name in RISK_BANDS_PD:
        if pd_ < thr:
            return name
    return "High"


def risk_category_from_score(score: int) -> str:
    return "Low" if score >= 720 else "Moderate" if score >= 660 else "Elevated" if score >= 580 else "High"


# ----------------------------------------------------------------------------- training
def _model_path() -> Path:
    return Path(get_settings().models_dir) / f"{MODEL_VERSION}.joblib"


def train_credit_model(seed: int = 7) -> dict[str, Any]:
    from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix, precision_score, recall_score, roc_auc_score)
    from sklearn.model_selection import train_test_split
    from xgboost import XGBClassifier

    X, y, _ = make_credit_dataset(9000, seed)
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=seed, stratify=y)
    X_tr, X_va, y_tr, y_va = train_test_split(X_tr, y_tr, test_size=0.15, random_state=seed, stratify=y_tr)
    model = XGBClassifier(n_estimators=400, max_depth=3, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9, min_child_weight=3,
                          reg_lambda=2.0, eval_metric="logloss", early_stopping_rounds=30, random_state=seed, n_jobs=2, tree_method="hist")
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
    p = model.predict_proba(X_te)[:, 1]
    thr = 0.15
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_te, pred, labels=[0, 1]).ravel()
    # expected calibration error (10 equal-frequency bins)
    order = np.argsort(p)
    bins = np.array_split(order, 10)
    ece = float(sum(len(b) / len(p) * abs(p[b].mean() - y_te[b].mean()) for b in bins))
    metrics = {
        "auc": float(roc_auc_score(y_te, p)), "average_precision": float(average_precision_score(y_te, p)),
        "precision_at_0.15": float(precision_score(y_te, pred, zero_division=0)), "recall_at_0.15": float(recall_score(y_te, pred, zero_division=0)),
        "brier": float(brier_score_loss(y_te, p)), "ece": ece,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp), "threshold": thr},
        "base_default_rate": float(y.mean()), "n_train": int(len(y_tr)), "n_test": int(len(y_te)), "best_iteration": int(getattr(model, "best_iteration", 0) or 0),
    }
    bundle = {"model": model, "features": CREDIT_FEATURES, "metrics": metrics, "version": MODEL_VERSION,
              "importance": {f: float(i) for f, i in zip(CREDIT_FEATURES, model.feature_importances_)}}
    joblib.dump(bundle, _model_path())
    return bundle


def load_model(train_if_missing: bool = True) -> dict[str, Any] | None:
    global _bundle
    with _lock:
        if _bundle is not None:
            return _bundle
        path = _model_path()
        try:
            if path.exists():
                _bundle = joblib.load(path)
            elif train_if_missing:
                _bundle = train_credit_model()
        except Exception:  # noqa: BLE001 - any load/train failure => scorecard fallback
            _bundle = None
        return _bundle


def reset_model_cache() -> None:
    global _bundle, _explainer
    with _lock:
        _bundle, _explainer = None, None


# ----------------------------------------------------------------------------- explanations
def _fmt(v: float, kind: str) -> str:
    return {"pct": f"{v:.0%}", "int": f"{v:.0f}", "f1": f"{v:.1f}", "f2": f"{v:.2f}"}[kind]


# feature -> (risk-increasing text, risk-decreasing text, value format)
FACTOR_TEXT: dict[str, tuple[str, str, str]] = {
    "bureau_score": ("Bureau score of {v} is below the 700 comfort level", "Bureau score of {v} is above the 700 comfort level", "int"),
    "bounce_count_90d": ("{v} bounced payment(s) in the last 90 days", "No bounced payments in the last 90 days", "int"),
    "utilization": ("Credit utilization is high at {v}", "Comfortable credit utilization of {v}", "pct"),
    "utilization_change_90d": ("Utilization has risen {v} over 90 days", "Utilization is stable or falling over 90 days", "pct"),
    "repayment_delay_count_6m": ("{v} delayed EMI payment(s) in the last 6 months", "Clean repayment record over the last 6 months", "int"),
    "inflow_trend_pct": ("Receipts are trending down ({v}% a month)", "Receipts are growing ({v}% a month)", "f1"),
    "inflow_cv": ("Weekly receipts are volatile (CV {v})", "Stable weekly receipts (CV {v})", "f2"),
    "net_margin": ("Thin cash margin of {v}", "Healthy cash margin of {v}", "pct"),
    "top_customer_share": ("{v} of receipts come from a single customer", "Receipts are well diversified (top customer {v})", "pct"),
    "dscr": ("Debt-service cover of {v}x is thin", "Comfortable debt-service cover ({v}x)", "f1"),
    "liquidity_cover_days": ("Only {v} day(s) of cash cover", "{v} day(s) of cash cover", "int"),
    "business_age_years": ("Young business ({v} years trading)", "Established business ({v} years trading)", "f1"),
    "requested_to_inflow": ("Requested amount is {v}x monthly receipts", "Requested amount is modest at {v}x monthly receipts", "f1"),
    "avg_receipt_delay_30d": ("Customers pay {v} days late on average", "Customers pay close to terms ({v} days late)", "int"),
    "log_avg_monthly_inflow": ("Smaller scale of monthly receipts", "Larger scale of monthly receipts", "int"),
}


# neutral reference level and which side is risky - used so explanations never contradict the data
_REF = {"bureau_score": (700, "low"), "bounce_count_90d": (0.5, "high"), "utilization": (0.5, "high"), "utilization_change_90d": (0.0, "high"),
        "repayment_delay_count_6m": (0.5, "high"), "inflow_trend_pct": (0.0, "low"), "inflow_cv": (0.4, "high"), "net_margin": (0.15, "low"),
        "top_customer_share": (0.4, "high"), "dscr": (1.5, "low"), "liquidity_cover_days": (20, "low"), "business_age_years": (5, "low"),
        "requested_to_inflow": (3, "high"), "avg_receipt_delay_30d": (18, "high"), "log_avg_monthly_inflow": (16.0, "low")}


def _consistent(feat: str, value: float, contrib: float) -> bool:
    ref, risky = _REF[feat]
    on_risky_side = value < ref if risky == "low" else value > ref
    return on_risky_side if contrib > 0 else (not on_risky_side)


def _factor_entry(feat: str, value: float, contrib: float) -> dict:
    inc_txt, dec_txt, kind = FACTOR_TEXT[feat]
    v = abs(value) if feat == "inflow_trend_pct" else value
    shown = _fmt(v, kind)
    text = (inc_txt if contrib > 0 else dec_txt).replace("{v}", shown)
    return {"feature": feat, "value": round(float(value), 4), "contribution": round(float(contrib), 4), "text": text}


def shap_contributions(model: Any, x: np.ndarray) -> tuple[np.ndarray, float, str]:
    """TreeSHAP contributions in log-odds space. Returns (values, base_value, method)."""
    global _explainer
    try:
        import shap

        with _lock:
            if _explainer is None:
                _explainer = shap.TreeExplainer(model)
        sv = _explainer.shap_values(x)
        sv = np.asarray(sv)
        if sv.ndim == 3:
            sv = sv[..., -1]
        base = _explainer.expected_value
        base = float(np.ravel(base)[-1])
        return sv[0], base, "shap.TreeExplainer (TreeSHAP)"
    except Exception:  # noqa: BLE001
        import xgboost as xgb

        contribs = model.get_booster().predict(xgb.DMatrix(x, feature_names=None), pred_contribs=True)
        return contribs[0, :-1], float(contribs[0, -1]), "TreeSHAP via xgboost pred_contribs"


# ----------------------------------------------------------------------------- scorecard fallback
def scorecard(f: dict[str, Any]) -> tuple[int, list[dict]]:
    pts: list[tuple[str, float]] = [
        ("bureau_score", (f["bureau_score"] - 700) * 0.55),
        ("bounce_count_90d", -min(f["bounce_count_90d"], 5) * 22),
        ("utilization", (0.5 - f["utilization"]) * 110),
        ("utilization_change_90d", -max(f["utilization_change_90d"], 0) * 220),
        ("repayment_delay_count_6m", -min(f["repayment_delay_count_6m"], 5) * 24),
        ("inflow_trend_pct", float(np.clip(f["inflow_trend_pct"], -8, 8)) * 4),
        ("inflow_cv", -(f["inflow_cv"] - 0.4) * 70),
        ("net_margin", float(np.clip(f["net_margin"], -0.1, 0.25)) * 260),
        ("top_customer_share", -max(f["top_customer_share"] - 0.4, 0) * 90),
        ("dscr", (min(f["dscr"], 4) - 1.5) * 16),
        ("liquidity_cover_days", (min(f["liquidity_cover_days"], 45) - 20) * 0.7),
        ("business_age_years", (min(f["business_age_years"], 12) - 5) * 3),
        ("requested_to_inflow", -max(f["requested_to_inflow"] - 3, 0) * 9),
        ("avg_receipt_delay_30d", -max(f["avg_receipt_delay_30d"] - 15, 0) * 1.1),
    ]
    score = int(round(min(900, max(300, 650 + sum(p for _, p in pts)))))
    contribs = [{"feature": k, "points": round(p, 1)} for k, p in pts]
    return score, contribs


def _applicable(features: dict[str, Any], feat: str) -> bool:
    """Hide factors that are not meaningful for this SME (e.g. utilisation without a facility)."""
    if feat in ("utilization", "utilization_change_90d") and not features.get("has_facility_history"):
        return False
    if feat == "dscr" and features.get("monthly_debt_service", 0) <= 0:
        return False
    return True


def assess(features: dict[str, Any], force_fallback: bool = False) -> dict[str, Any]:
    """Return model score / PD / category / factors for one feature dict."""
    bundle = None if force_fallback else load_model()
    if bundle is not None:
        x = np.array([[float(features[k]) for k in CREDIT_FEATURES]])
        model = bundle["model"]
        pd_ = float(model.predict_proba(x)[0, 1])
        sv, base, method = shap_contributions(model, x)
        entries = [_factor_entry(k, features[k], sv[i]) for i, k in enumerate(CREDIT_FEATURES) if _applicable(features, k)]
        risk = sorted([e for e in entries if e["contribution"] > 0.03 and _consistent(e["feature"], e["value"], e["contribution"])], key=lambda e: -e["contribution"])[:5]
        pos = sorted([e for e in entries if e["contribution"] < -0.03 and _consistent(e["feature"], e["value"], e["contribution"])], key=lambda e: e["contribution"])[:5]
        return {
            "model_score": pd_to_score(pd_), "pd_estimate": round(pd_, 4), "pd_source": "xgboost_trained_on_synthetic_data",
            "risk_category": risk_category_from_pd(pd_), "risk_factors": risk, "positive_factors": pos,
            "explanation_type": "shap", "explanation_method": method, "base_log_odds": round(base, 4),
            "model_version": bundle["version"], "model_available": True,
        }
    score, contribs = scorecard(features)
    entries = []
    for c in [c for c in contribs if _applicable(features, c["feature"])]:
        e = _factor_entry(c["feature"], features[c["feature"]], -c["points"])  # points>0 reduce risk
        e["contribution"] = round(-c["points"], 1)
        entries.append(e)
    risk = sorted([e for e in entries if e["contribution"] > 3], key=lambda e: -e["contribution"])[:5]
    pos = sorted([e for e in entries if e["contribution"] < -3], key=lambda e: e["contribution"])[:5]
    return {
        "model_score": score, "pd_estimate": None, "pd_source": "none_scorecard_fallback", "risk_category": risk_category_from_score(score),
        "risk_factors": risk, "positive_factors": pos, "explanation_type": "rule_based", "explanation_method": "Deterministic points scorecard (rule-based)",
        "base_log_odds": None, "model_version": "credit-scorecard-v1", "model_available": False,
    }


def suggested_limit(features: dict[str, Any], category: str, policy_outcome: str) -> tuple[float, float]:
    mult = {"Low": 2.6, "Moderate": 1.9, "Elevated": 1.0, "High": 0.4}[category]
    if policy_outcome == "not_eligible":
        return 0.0, 0.0
    cap = features["avg_monthly_inflow"] * mult
    if features["dscr"] < 1.2:
        cap *= 0.6
    high = round(cap / 50_000) * 50_000
    return float(round(high * 0.6 / 50_000) * 50_000), float(high)


def narrative(sme_name: str, a: dict, policy: dict, req: float) -> str:
    """Plain-language explanation assembled from the actual factors (no LLM required)."""
    pd_txt = f", with an estimated synthetic-model PD of {a['pd_estimate']:.1%}" if a["pd_estimate"] is not None else " (scorecard fallback - no PD available)"
    _j = lambda xs: "; ".join(str(x).strip().rstrip(".") for x in xs)
    pos = _j(f["text"] for f in a["positive_factors"][:3]) or "no strong positives identified"
    neg = _j(f["text"] for f in a["risk_factors"][:3]) or "no material risk drivers identified"
    out = {"eligible": "eligible for further consideration", "refer": "referred for human review", "not_eligible": "not eligible under the demo policy"}[policy["outcome"]]
    trig = ""
    if policy["triggered"]:
        trig = " Policy flags: " + _j(r["message"] for r in policy["triggered"][:3]) + "."
    kind = "SHAP-based" if a["explanation_type"] == "shap" else "rule-based"
    return (f"{sme_name} scores {a['model_score']} (risk category {a['risk_category']}){pd_txt}. Supporting factors: {pos}. Main risk drivers: {neg}. "
            f"Under the demo policy the case is {out}.{trig} This is a {kind} explanation of a prototype model trained on synthetic data - it is a preliminary assessment, "
            f"not a bank credit decision.")
