from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Ctx, get_ctx, get_db, require_roles, staff
from app.config import get_settings
from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.db import synthetic as syn
from app.ml import credit as credit_ml
from app.ml import forecast as forecast_ml
from app.ml import kyc as kyc_ml
from app.ml import kyc_docs
from app.ml import propensity as prop_ml
from app.ml.features import build_features
from app.models.entities import (SME, Alert, CashflowForecast, CreditApplication, CreditAssessment, Document, KYCApplication, ProductRecommendation, RMFeedback, new_id, utcnow)
from app.repositories import data as repo
from app.services import analysis, kyc_service
from app.services import policy as policy_svc
from app.services.audit import log_audit
from app.services.views import sme_summary

router = APIRouter(prefix="/api", dependencies=[Depends(staff)])


def _sme(db: Session, sme_id: str) -> SME:
    s = db.get(SME, sme_id)
    if not s:
        raise HTTPException(404, f"SME {sme_id} not found")
    return s


# ============================================================================ prospecting
@router.get("/prospecting/leads", tags=["prospecting"])
def leads(segment: str | None = None, product: str | None = None, consent: str | None = None, min_score: float = 0, industry: str | None = None, db: Session = Depends(get_db)):
    rows = []
    for s in db.scalars(select(SME)):
        rec = repo.latest_recommendation(db, s.id)
        if not rec or s.onboarding_status == "onboarding":
            continue
        if segment and s.segment != segment or consent and s.consent_status != consent or industry and s.industry != industry or product and rec.product != product or rec.propensity_score < min_score:
            continue
        rows.append({**sme_summary(s), "recommendation_id": rec.id, "product": rec.product, "product_label": prop_ml.PRODUCT_LABELS[rec.product], "propensity_score": rec.propensity_score,
                     "est_value": rec.est_value, "rationale": rec.rationale, "indicators": rec.indicators, "outreach_action": rec.outreach_action, "outreach_allowed": rec.outreach_allowed,
                     "scores": rec.all_scores, "generated_at": rec.created_at.isoformat(timespec="seconds")})
    rows.sort(key=lambda r: (-int(r["outreach_allowed"]), -r["propensity_score"]))
    return {"leads": rows, "products": [{"id": k, "label": v} for k, v in prop_ml.PRODUCT_LABELS.items()],
            "model_note": "Propensity scores come from a one-vs-rest model trained on synthetic labels. They are uncalibrated ranking scores, not probabilities of conversion.",
            "data_note": "Prospect data is simulated account-aggregator data."}


@router.post("/prospecting/{sme_id}/analyze", tags=["prospecting"])
def prospect_analyze(sme_id: str, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    sme = repo.get_sme_dict(db, _sme(db, sme_id).id)
    feats = build_features(sme, repo.load_txns(db, sme_id, get_as_of_date()), get_as_of_date())
    rec = prop_ml.recommend(feats, sme)
    analysis.persist_recommendation(db, sme, None, rec, ctx.user, ctx.role)
    db.commit()
    return to_jsonable(rec)


@router.post("/prospecting/{sme_id}/outreach", tags=["prospecting"])
def outreach(sme_id: str, body: dict, ctx: Ctx = Depends(require_roles("Relationship Manager")), db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    if s.consent_status != "granted":
        raise HTTPException(403, f"Outreach blocked: marketing / data-use consent is '{s.consent_status}'.")
    log_audit(db, actor=ctx.user, role=ctx.role, action="outreach_planned", category="prospecting", entity_type="sme", entity_id=sme_id, sme_id=sme_id,
              summary=f"{ctx.user} planned outreach for {s.name}: {body.get('action', 'follow-up')}", details=body)
    db.commit()
    return {"ok": True, "note": "Recorded in the audit trail only - no message is sent by this prototype."}


# ============================================================================ KYC
@router.get("/kyc/applications", tags=["kyc"])
def kyc_list(db: Session = Depends(get_db)):
    return kyc_service.list_view(db)


@router.post("/kyc/applications", tags=["kyc"])
def kyc_create(body: dict, ctx: Ctx = Depends(require_roles("KYC Analyst", "Relationship Manager")), db: Session = Depends(get_db)):
    s = _sme(db, body.get("sme_id", ""))
    existing = repo.latest_kyc(db, s.id)
    if existing and existing.status not in ("rejected",):
        return kyc_service.application_view(db, existing, ctx.role)
    app = kyc_service.get_or_create_application(db, s.id)
    log_audit(db, actor=ctx.user, role=ctx.role, action="kyc_application_opened", category="kyc", entity_type="kyc_application", entity_id=app.id, sme_id=s.id, summary=f"KYC application opened for {s.name}")
    db.commit()
    return kyc_service.application_view(db, app, ctx.role)


@router.get("/kyc/applications/{app_id}", tags=["kyc"])
def kyc_get(app_id: str, reveal: bool = False, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    app = db.get(KYCApplication, app_id)
    if not app:
        raise HTTPException(404, "KYC application not found")
    if reveal and ctx.can_reveal:
        log_audit(db, actor=ctx.user, role=ctx.role, action="pii_revealed", category="kyc", entity_type="kyc_application", entity_id=app.id, sme_id=app.sme_id,
                  summary=f"{ctx.role} revealed masked identifiers on the KYC case", severity="attention")
        db.commit()
    return kyc_service.application_view(db, app, ctx.role, reveal)


@router.post("/kyc/applications/{app_id}/documents", tags=["kyc"])
async def kyc_upload(app_id: str, file: UploadFile = File(...), doc_type: str | None = Form(None), ctx: Ctx = Depends(require_roles("KYC Analyst", "Relationship Manager")),
                     db: Session = Depends(get_db)):
    app = db.get(KYCApplication, app_id)
    if not app:
        raise HTTPException(404, "KYC application not found")
    if app.status in kyc_service.TERMINAL:
        raise HTTPException(409, f"The application is '{app.status}'; documents can no longer be added.")
    content = await file.read()
    if doc_type and doc_type not in kyc_ml.DOC_TYPES:
        raise HTTPException(400, f"Unknown document type '{doc_type}'")
    chk = kyc_ml.validate_file(file.filename or "upload", content, file.content_type)
    if not chk.ok:
        raise HTTPException(400, chk.error)
    try:
        res = kyc_service.ingest_document(db, app_id, file.filename or "upload", content, file.content_type, doc_type, ctx.user, ctx.role)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return res


@router.patch("/kyc/documents/{doc_id}", tags=["kyc"])
def kyc_correct(doc_id: str, body: dict, ctx: Ctx = Depends(require_roles("KYC Analyst")), db: Session = Depends(get_db)):
    try:
        res = kyc_service.correct_document(db, doc_id, body.get("corrections") or {}, ctx.user, ctx.role)
    except LookupError:
        raise HTTPException(404, "Document not found")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return res


@router.post("/kyc/applications/{app_id}/evaluate", tags=["kyc"])
def kyc_eval(app_id: str, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    app = db.get(KYCApplication, app_id)
    if not app:
        raise HTTPException(404, "KYC application not found")
    res = kyc_service.evaluate_application(db, app, actor=ctx.user, note="Re-evaluated on request")
    db.commit()
    return res


@router.get("/kyc/documents/{doc_id}/file", tags=["kyc"])
def kyc_file(doc_id: str, ctx: Ctx = Depends(require_roles("KYC Analyst", "Credit Analyst")), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d or not d.file_path or not Path(d.file_path).exists():
        raise HTTPException(404, "Original file not stored (synthetic legacy record).")
    log_audit(db, actor=ctx.user, role=ctx.role, action="document_viewed", category="kyc", entity_type="document", entity_id=d.id, sme_id=d.sme_id, summary=f"{ctx.role} opened the original document {d.filename}")
    db.commit()
    return FileResponse(d.file_path, media_type=d.mime_type, filename=d.filename)


def _sample_defs(db: Session, sme_id: str) -> list[dict]:
    plan = kyc_docs.ONBOARDING_PLAN.get(sme_id)
    out = []
    for dt in kyc_ml.REQUIRED_DOCS:
        out.append({"key": f"{sme_id}:{dt}:clean", "doc_type": dt, "variant": "clean", "label": f"{kyc_ml.DOC_LABELS[dt]} (clean sample)", "note": "Consistent with the SME master record."})
    if plan and plan["flaws"]:
        for dt in plan["docs"]:
            out.append({"key": f"{sme_id}:{dt}:flawed", "doc_type": dt, "variant": "flawed", "label": f"{kyc_ml.DOC_LABELS[dt]} (as submitted)", "note": "Same document the onboarding case was seeded with."})
    return out


@router.get("/kyc/samples/{sme_id}", tags=["kyc"])
def kyc_samples(sme_id: str, db: Session = Depends(get_db)):
    _sme(db, sme_id)
    return {"banner": "All sample documents are SYNTHETIC and carry a visible banner.", "samples": _sample_defs(db, sme_id)}


def _build_sample(db: Session, key: str, fmt: str = "pdf"):
    sme_id, dt, variant = key.split(":")
    sme = _sme(db, sme_id)
    r = next((x for x in syn.ROSTER if x["id"] == sme_id), None)
    if not r:
        raise HTTPException(404, "No sample generator for this SME")
    ident = syn.identity_for(r, get_settings().seed_rng)
    flaws = (kyc_docs.ONBOARDING_PLAN.get(sme_id) or {}).get("flaws", {}) if variant == "flawed" else {}
    return kyc_docs.build_document(dt, r, ident, get_as_of_date(), flaws, fmt)


@router.get("/kyc/samples/download/{key}", tags=["kyc"])
def kyc_sample_download(key: str, db: Session = Depends(get_db)):
    name, content, mime = _build_sample(db, key)
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/kyc/applications/{app_id}/load-sample", tags=["kyc"])
def kyc_load_sample(app_id: str, body: dict, ctx: Ctx = Depends(require_roles("KYC Analyst", "Relationship Manager")), db: Session = Depends(get_db)):
    app = db.get(KYCApplication, app_id)
    if not app:
        raise HTTPException(404, "KYC application not found")
    name, content, mime = _build_sample(db, body["key"])
    res = kyc_service.ingest_document(db, app_id, name, content, mime, body["key"].split(":")[1], ctx.user, ctx.role)
    db.commit()
    return res


# ============================================================================ credit
def _assessment_view(a: CreditAssessment) -> dict:
    return {"id": a.id, "sme_id": a.sme_id, "application_id": a.application_id, "run_id": a.run_id, "requested_amount": a.requested_amount, "tenure_months": a.tenure_months,
            "model_score": a.model_score, "pd_estimate": a.pd_estimate, "pd_source": a.pd_source, "risk_category": a.risk_category, "policy_outcome": a.policy_outcome,
            "policy_rules": a.policy_rules, "limit_low": a.limit_low, "limit_high": a.limit_high, "positive_factors": a.positive_factors, "risk_factors": a.risk_factors,
            "explanation": a.explanation, "explanation_type": a.explanation_type, "model_version": a.model_version, "human_review_status": a.human_review_status, "features": a.features,
            "created_at": a.created_at.isoformat(timespec="seconds")}


@router.get("/credit/portfolio", tags=["credit"])
def credit_portfolio(db: Session = Depends(get_db)):
    rows = []
    for s in db.scalars(select(SME).where(SME.onboarding_status != "prospect")):
        a = repo.latest_assessment(db, s.id)
        app = repo.open_credit_application(db, s.id)
        if not a and not app:
            continue
        rows.append({**sme_summary(s), "assessment": _assessment_view(a) if a else None,
                     "application": {"id": app.id, "requested_amount": app.requested_amount, "tenure_months": app.tenure_months, "purpose": app.purpose, "status": app.status} if app else None,
                     "kyc_status": (repo.latest_kyc(db, s.id).status if repo.latest_kyc(db, s.id) else "not_started")})
    return {"rows": rows, "model": credit_ml.MODEL_VERSION, "disclaimer": "Prototype assessments trained on synthetic data - not bank credit decisions."}


@router.get("/credit/{sme_id}", tags=["credit"])
def credit_detail(sme_id: str, db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    a = repo.latest_assessment(db, sme_id)
    app = repo.open_credit_application(db, sme_id)
    hist = db.scalars(select(CreditAssessment).where(CreditAssessment.sme_id == sme_id).order_by(CreditAssessment.created_at.desc()).limit(8)).all()
    return {"sme": sme_summary(s), "assessment": _assessment_view(a) if a else None, "history": [{"id": h.id, "model_score": h.model_score, "policy_outcome": h.policy_outcome, "created_at": h.created_at.isoformat(timespec="seconds"),
                                                                                                 "risk_category": h.risk_category} for h in hist],
            "application": {"id": app.id, "requested_amount": app.requested_amount, "tenure_months": app.tenure_months, "purpose": app.purpose, "status": app.status} if app else None,
            "kyc": kyc_service.kyc_summary_for_policy(db, sme_id), "model_available": analysis.credit_model_enabled(db), "disclaimer": "Preliminary demo assessment - not a bank credit decision."}


@router.post("/credit/{sme_id}/assess", tags=["credit"])
def credit_assess(sme_id: str, body: dict, persist: bool = False, ctx: Ctx = Depends(require_roles("Credit Analyst", "Relationship Manager", "Risk Manager")), db: Session = Depends(get_db)):
    _sme(db, sme_id)
    try:
        out = analysis.assess_and_persist_direct(db, sme_id, body, ctx.user, ctx.role, persist=persist)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return to_jsonable(out)


@router.put("/credit/model-availability", tags=["credit"])
def model_availability(body: dict, ctx: Ctx = Depends(require_roles("Risk Manager", "Credit Analyst")), db: Session = Depends(get_db)):
    from app.models.entities import AppSetting

    db.merge(AppSetting(key="credit_model_available", value=bool(body.get("available", True))))
    log_audit(db, actor=ctx.user, role=ctx.role, action="credit_model_toggle", category="system", entity_type="setting", entity_id="credit_model_available",
              summary=f"Credit model {'enabled' if body.get('available', True) else 'disabled (scorecard fallback test)'} by {ctx.user}", severity="attention")
    db.commit()
    return {"available": bool(body.get("available", True))}


# ============================================================================ cash flow
def _fc_inputs(db: Session, sme_id: str):
    s = _sme(db, sme_id)
    as_of = get_as_of_date()
    return repo.get_sme_dict(db, sme_id), repo.load_txns(db, sme_id, as_of), as_of


@router.get("/cashflow/{sme_id}", tags=["cashflow"])
def cashflow(sme_id: str, horizon: int = Query(60, ge=7, le=180), db: Session = Depends(get_db)):
    sme, txns, as_of = _fc_inputs(db, sme_id)
    if len(txns) < 20:
        return {"available": False, "reason": f"Only {len(txns)} transactions on file - too few to forecast reliably."}
    fc = analysis.compute_forecast(sme, txns, as_of, horizon, None)
    return {"available": True, "sme": {"id": sme["id"], "name": sme["name"], "sanctioned_limit": sme["sanctioned_limit"], "utilization": sme["utilization"]}, "forecast": to_jsonable(fc),
            "scenario_levers": forecast_ml.DEFAULT_SCENARIO}


@router.post("/cashflow/{sme_id}/scenario", tags=["cashflow"])
def cashflow_scenario(sme_id: str, body: dict, db: Session = Depends(get_db)):
    sme, txns, as_of = _fc_inputs(db, sme_id)
    horizon = int(body.get("horizon", 60))
    base = analysis.compute_forecast(sme, txns, as_of, horizon, None)
    sc = analysis.compute_forecast(sme, txns, as_of, horizon, body.get("scenario") or {})
    b, s = base["summary"], sc["summary"]
    return {"baseline": to_jsonable(base), "scenario": to_jsonable(sc), "delta": {"ending_balance": s["expected_ending_balance"] - b["expected_ending_balance"], "min_balance": s["min_balance"] - b["min_balance"],
                                                                                    "days_to_shortfall": (None if s["days_to_shortfall"] is None or b["days_to_shortfall"] is None else s["days_to_shortfall"] - b["days_to_shortfall"])}}


@router.post("/cashflow/{sme_id}/save", tags=["cashflow"])
def cashflow_save(sme_id: str, body: dict, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    sme, txns, as_of = _fc_inputs(db, sme_id)
    fc = analysis.compute_forecast(sme, txns, as_of, int(body.get("horizon", 60)), body.get("scenario"))
    fid = analysis.persist_forecast(db, sme, None, fc, ctx.user, ctx.role)
    db.commit()
    return {"id": fid, "is_baseline": fc["is_baseline"]}


@router.get("/cashflow/{sme_id}/backtest", tags=["cashflow"])
def cashflow_backtest(sme_id: str, holdout: int = Query(30, ge=7, le=60), db: Session = Depends(get_db)):
    sme, txns, as_of = _fc_inputs(db, sme_id)
    bt = forecast_ml.backtest(txns, as_of, holdout)
    if bt is None:
        return {"available": False, "reason": "Fewer than 60 days of history before the hold-out window - back-test not possible."}
    return {"available": True, **to_jsonable(bt)}


# ============================================================================ EWS
def _alert_view(a: Alert, s: SME) -> dict:
    return {"id": a.id, "sme_id": a.sme_id, "sme_name": s.name, "industry": s.industry, "city": s.city, "priority_score": a.priority_score, "priority_level": a.priority_level,
            "components": a.components, "risk_category": a.risk_category, "exposure": a.exposure, "primary_signal": a.primary_signal, "signals": a.signals, "days_to_stress": a.days_to_stress,
            "recommended_action": a.recommended_action, "talking_points": a.talking_points, "assigned_rm": a.assigned_rm, "review_status": a.review_status, "missing_data": a.missing_data,
            "created_at": a.created_at.isoformat(timespec="seconds"), "updated_at": a.updated_at.isoformat(timespec="seconds"), "run_id": a.run_id}


@router.get("/ews/alerts", tags=["ews"])
def ews_alerts(status: str | None = None, level: str | None = None, rm: str | None = None, db: Session = Depends(get_db)):
    q = select(Alert, SME).join(SME, SME.id == Alert.sme_id).order_by(Alert.priority_score.desc())
    if status and status != "all":
        q = q.where(Alert.review_status.in_(status.split(",")))
    elif not status:
        q = q.where(Alert.review_status.in_(["open", "in_review", "escalated"]))
    if level:
        q = q.where(Alert.priority_level == level)
    if rm:
        q = q.where(Alert.assigned_rm == rm)
    return {"alerts": [_alert_view(a, s) for a, s in db.execute(q)], "rms": sorted({r[0] for r in db.execute(select(Alert.assigned_rm).distinct())}),
            "priority_formula": "priority = risk (0-40) + exposure (0-25) + urgency (0-25) + actionability (0-10)",
            "data_note": "Utilization-based signals use SIMULATED facility history. Bounce, balance and turnover signals come from the synthetic transaction feed."}


@router.get("/ews/alerts/{alert_id}", tags=["ews"])
def ews_alert(alert_id: str, db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    s = db.get(SME, a.sme_id)
    fb = db.scalars(select(RMFeedback).where(RMFeedback.alert_id == a.id).order_by(RMFeedback.created_at.desc())).all()
    fc = repo.latest_forecast(db, a.sme_id)
    return {**_alert_view(a, s), "feedback": [{"id": f.id, "rm_name": f.rm_name, "action_taken": f.action_taken, "outcome": f.outcome, "notes": f.notes, "created_at": f.created_at.isoformat(timespec="seconds")} for f in fb],
            "forecast_headline": to_jsonable(fc.result["summary"]) if fc else None, "sme": sme_summary(s)}


@router.post("/ews/alerts/{alert_id}/feedback", tags=["ews"])
def ews_feedback(alert_id: str, body: dict, ctx: Ctx = Depends(require_roles("Relationship Manager", "Risk Manager", "Credit Analyst")), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    outcome = body.get("outcome", "")
    if outcome not in ("resolved", "improving", "no_change", "worsening", "escalated"):
        raise HTTPException(400, "outcome must be one of resolved, improving, no_change, worsening, escalated")
    if not (body.get("action_taken") or "").strip():
        raise HTTPException(400, "Describe the action taken.")
    db.add(RMFeedback(id=new_id("FBK"), alert_id=a.id, sme_id=a.sme_id, rm_name=ctx.user, action_taken=body["action_taken"].strip(), outcome=outcome, notes=(body.get("notes") or "").strip()))
    a.review_status = {"resolved": "resolved", "escalated": "escalated"}.get(outcome, "in_review")
    a.updated_at = utcnow()
    log_audit(db, actor=ctx.user, role=ctx.role, action="alert_feedback", category="alert", entity_type="alert", entity_id=a.id, sme_id=a.sme_id,
              summary=f"{ctx.user} logged '{body['action_taken'].strip()[:80]}' on alert for {db.get(SME, a.sme_id).name}: {outcome}", details={"outcome": outcome})
    db.commit()
    return {"ok": True, "review_status": a.review_status}


@router.post("/ews/scan", tags=["ews"])
def ews_scan(ctx: Ctx = Depends(require_roles("Risk Manager", "Relationship Manager")), db: Session = Depends(get_db)):
    """Re-score every active client with the same compute functions used by the workflow."""
    as_of = get_as_of_date()
    n = 0
    for s in db.scalars(select(SME).where(SME.segment == "client", SME.onboarding_status == "active")):
        sme = repo.get_sme_dict(db, s.id)
        txns = repo.load_txns(db, s.id, as_of)
        feats = build_features(sme, txns, as_of)
        fc = analysis.compute_forecast(sme, txns, as_of, 60, None)
        credit = credit_ml.assess(feats)
        risk = analysis.compute_risk(sme, feats, fc, credit, analysis.recent_feedback(db, s.id))
        if analysis.persist_risk(db, sme, None, risk, None, ctx.user, ctx.role):
            n += 1
    db.commit()
    return {"scanned_at": utcnow().isoformat(timespec="seconds"), "alerts_updated": n}
