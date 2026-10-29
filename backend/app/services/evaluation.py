"""Model performance: only metrics that can honestly be computed here. Everything is labelled with its
dataset (synthetic) and anything that cannot be computed returns available=False with a reason."""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import select

from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.db.session import session_scope
from app.ml import credit as credit_ml
from app.ml import forecast as forecast_ml
from app.ml import kyc as kyc_ml
from app.ml import kyc_docs
from app.ml import propensity as prop_ml
from app.ml.training_data import CREDIT_FEATURES, make_credit_dataset, sample_sme_features
from app.models.entities import SME, AppSetting, CreditAssessment, utcnow
from app.repositories import data as repo


def _credit() -> dict:
    b = credit_ml.load_model()
    if not b:
        return {"available": False, "reason": "The credit model could not be loaded; the deterministic scorecard fallback is in use and has no statistical metrics."}
    from sklearn.metrics import roc_curve

    X, y, _ = make_credit_dataset(4000, 99)
    p = b["model"].predict_proba(X)[:, 1]
    fpr, tpr, _ = roc_curve(y, p)
    idx = np.linspace(0, len(fpr) - 1, 40).astype(int)
    order = np.argsort(p)
    cal = [{"predicted": float(p[bn].mean()), "observed": float(y[bn].mean()), "n": int(len(bn))} for bn in np.array_split(order, 8)]
    # KS statistic
    ks = _ks(p, y)
    return {"available": True, "model_version": b["version"], "dataset": "synthetic (held-out sample regenerated with a different seed)", "metrics": {**b["metrics"], "ks": ks},
            "roc": [{"fpr": float(fpr[i]), "tpr": float(tpr[i])} for i in idx], "calibration": cal,
            "importance": sorted(({"feature": k, "importance": v} for k, v in b["importance"].items()), key=lambda r: -r["importance"]),
            "caveat": "Labels are synthetic and generated from a known risk function, so these metrics describe the demo pipeline - they say nothing about real-world default prediction."}


def _ks(p: np.ndarray, y: np.ndarray) -> float:
    o = np.argsort(p)
    ys = y[o]
    cp = np.cumsum(ys) / max(1, ys.sum())
    cn = np.cumsum(1 - ys) / max(1, (1 - ys).sum())
    return float(np.max(np.abs(cp - cn)))


def _propensity() -> dict:
    b = prop_ml.load()
    if not b:
        return {"available": False, "reason": "Propensity model could not be loaded; rule-based ranking is in use."}
    return {"available": True, "model_version": b["version"], "dataset": "synthetic labels (held-out 25%)", "metrics": b["metrics"],
            "caveat": "Scores are an uncalibrated ranking, not conversion probabilities."}


def _forecast() -> dict:
    as_of = get_as_of_date()
    rows = []
    with session_scope() as s:
        for sme in s.scalars(select(SME).where(SME.segment == "client", SME.onboarding_status == "active")):
            bt = forecast_ml.backtest(repo.load_txns(s, sme.id, as_of), as_of, 30)
            if bt and bt["mae_pct_of_monthly_outflow"] is not None:
                rows.append({"sme_id": sme.id, "name": sme.name, **bt})
    if not rows:
        return {"available": False, "reason": "No client has enough history for a 30-day hold-out back-test."}
    pred = [r["predicted_low"] for r in rows]
    act = [r["actual_low"] for r in rows]
    tp = sum(1 for a, p in zip(act, pred) if a and p)
    fn = sum(1 for a, p in zip(act, pred) if a and not p)
    fp = sum(1 for a, p in zip(act, pred) if p and not a)
    return {"available": True, "dataset": "synthetic transactions, last 30 days held out", "n_smes": len(rows), "mean_mae_pct_of_monthly_outflow": float(np.mean([r["mae_pct_of_monthly_outflow"] for r in rows])),
            "median_mae_pct_of_monthly_outflow": float(np.median([r["mae_pct_of_monthly_outflow"] for r in rows])),
            "low_balance": {"actual_low": int(sum(act)), "caught": tp, "missed": fn, "false_alarms": fp, "recall": (tp / (tp + fn)) if (tp + fn) else None,
                            "note": "Recall is not available when no client actually dipped below its buffer in the hold-out window." if (tp + fn) == 0 else None},
            "per_sme": rows[:25], "caveat": "A baseline forecast built from earlier data cannot anticipate a shock that begins inside the hold-out window; the synthetic stress personas (e.g. receipts slowing in the last weeks) are exactly such cases, so missed low-balance events are expected and reported as-is.", "method": "EWMA weekday profile + detected recurring obligations (transparent baseline, not a learned model)"}


def _kyc() -> dict:
    from app.config import get_settings
    from app.db import synthetic as syn

    as_of = get_as_of_date()
    checks = {"PAN": ("pan", lambda i: i.pan), "GST": ("gstin", lambda i: i.gstin), "AADHAAR": ("aadhaar_last4", lambda i: i.aadhaar[-4:]),
              "INCORPORATION": ("registration_number", lambda i: i.registration_number), "BANK_STATEMENT": ("account_number", lambda i: i.account_number)}
    n = ok_cls = 0
    field_ok: dict[str, list[int]] = {k: [0, 0] for k in checks}
    for r in syn.ROSTER[:14]:
        ident = syn.identity_for(r, get_settings().seed_rng)
        for dt in kyc_ml.REQUIRED_DOCS:
            name, content, mime = kyc_docs.build_document(dt, r, ident, as_of, {}, "pdf")
            pdoc = kyc_ml.process_document(name, content, mime, as_of, None)
            n += 1
            ok_cls += int(pdoc.doc_type == dt)
            if dt in checks:
                f, truth = checks[dt]
                got = (pdoc.fields.get(f) or {}).get("value")
                field_ok[dt][1] += 1
                field_ok[dt][0] += int(str(got or "").replace(" ", "") == str(truth(ident)))
    return {"available": True, "dataset": "synthetic clean PDF documents generated by this prototype (text layer present)", "documents": n, "classification_accuracy": ok_cls / n,
            "field_accuracy": {k: (v[0] / v[1] if v[1] else None) for k, v in field_ok.items()},
            "caveat": "Accuracy on clean synthetic PDFs is an upper bound. Scanned images need OCR" + ("" if kyc_ml.ocr_available() else " (Tesseract is not installed here, so image uploads cannot be read).")}


def _psi(expected: np.ndarray, actual: np.ndarray, bins: int = 5) -> float:
    cuts = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)[1:-1]))
    e = np.bincount(np.searchsorted(cuts, expected), minlength=len(cuts) + 1) / len(expected)
    a = np.bincount(np.searchsorted(cuts, actual), minlength=len(cuts) + 1) / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def _drift() -> dict:
    with session_scope() as s:
        rows = [a.features for a in s.scalars(select(CreditAssessment)) if a.features]
    if len(rows) < 10:
        return {"available": False, "reason": f"Only {len(rows)} stored assessments; at least 10 are needed for a stable drift estimate."}
    ref = sample_sme_features(4000, np.random.default_rng(5))
    out = []
    for f in CREDIT_FEATURES:
        cur = np.array([float(r[f]) for r in rows if r.get(f) is not None])
        if len(cur) < 10 or f not in ref:
            continue
        v = _psi(np.asarray(ref[f], dtype=float), cur)
        out.append({"feature": f, "psi": v, "status": "stable" if v < 0.1 else ("watch" if v < 0.25 else "shifted")})
    return {"available": True, "n_current": len(rows), "reference": "synthetic training population", "features": sorted(out, key=lambda r: -r["psi"]),
            "caveat": "Small current sample (demo portfolio); PSI is indicative only. 'Shifted' here mostly reflects the demo universe differing from the training generator."}


def compute_all() -> dict[str, Any]:
    return to_jsonable({
        "computed_at": utcnow().isoformat(timespec="seconds"),
        "credit": _credit(), "propensity": _propensity(), "forecast": _forecast(), "kyc": _kyc(), "drift": _drift(),
        "ews": {"available": False, "reason": "Early-warning alerts have no labelled outcome history (no real defaults or confirmed stress events), so precision/recall cannot be computed. RM feedback is collected but too sparse to evaluate."},
    })


def get_cached(refresh: bool = False) -> dict:
    with session_scope() as s:
        row = s.get(AppSetting, "model_metrics_cache")
        if row and row.value and not refresh:
            return row.value
    data = compute_all()
    with session_scope() as s:
        s.merge(AppSetting(key="model_metrics_cache", value=data))
    return data
