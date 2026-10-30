"""SME Customer portal: the only area open to the SME Customer role. Shows the customer's own, plain-language view."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Ctx, get_ctx, get_db
from app.core.clock import get_as_of_date
from app.core.util import inr
from app.ml import kyc as kyc_ml
from app.models.entities import SME, AppSetting, CreditApplication
from app.repositories import data as repo
from app.services import analysis, kyc_service

router = APIRouter(prefix="/api/portal", tags=["portal"])


def _default_sme(db: Session) -> str:
    row = db.get(AppSetting, "portal_sme_id")
    return row.value if row else "SME-1003"


@router.get("/companies")
def companies(db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name} for s in db.scalars(select(SME).where(SME.segment == "client")) if s.onboarding_status in ("active", "onboarding")]


@router.get("/me")
def me(sme_id: str | None = None, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    sid = sme_id or _default_sme(db)
    s = db.get(SME, sid)
    if not s:
        raise HTTPException(404, "Company not found")
    kyc = repo.latest_kyc(db, sid)
    app = repo.open_credit_application(db, sid)
    a = repo.latest_assessment(db, sid)
    fc = repo.latest_forecast(db, sid)
    docs = kyc_service.application_view(db, kyc, "SME Customer", False) if kyc else None
    # customer-facing status wording: no scores, PDs or internal rule IDs
    credit_status = None
    if app:
        credit_status = {"id": app.id, "amount": app.requested_amount, "purpose": app.purpose, "status": {"submitted": "Received", "under_review": "Under review", "referred": "Under review by our credit team", "info_requested": "We need more information",
                                                                                                         "recommended_to_proceed": "Moving to the next stage", "declined_by_reviewer": "Not able to proceed", "not_eligible": "Not able to proceed"}.get(app.status, "Received")}
    return {"company": {"id": s.id, "name": s.name, "industry": s.industry, "city": s.city, "director": s.director_name, "rm": s.assigned_rm},
            "onboarding": ({"status": {"in_progress": "Documents needed", "info_requested": "We need an updated document", "exception": "Under review by our KYC team", "eligible_for_approval": "Awaiting final confirmation",
                                       "approved": "Complete", "rejected": "Not able to proceed", "not_started": "Not started"}.get(kyc.status, kyc.status),
                            "required": [{"label": d["label"], "uploaded": d["present"]} for d in docs["required_documents"]], "missing": kyc.missing_documents,
                            "requests": [e["message"] for e in (kyc.exceptions or []) if e["code"] in ("BILL_RECENCY", "REQUIRED_DOCUMENTS")]} if kyc else None),
            "credit_application": credit_status,
            "cash_flow": ({"horizon_days": fc.horizon_days, "message": ("Your forecast shows a possible cash shortfall around " + fc.shortfall_date.strftime("%d %b %Y") + ". Your relationship manager can help plan ahead.")
                           if fc.shortfall_date else "No cash shortfall is projected over the next 60 days.", "series": [{"date": p["date"], "balance": p["balance"]} for p in fc.result["series"]]} if fc else None),
            "recommendation": None, "balance": s.current_balance, "limit": s.sanctioned_limit, "as_of": get_as_of_date().isoformat(),
            "note": "Demo portal on synthetic data. Internal scores and risk assessments are never shown to customers."}


@router.post("/credit-request")
def credit_request(body: dict, sme_id: str | None = None, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    sid = sme_id or _default_sme(db)
    s = db.get(SME, sid)
    amount = float(body.get("amount") or 0)
    if amount < 100_000 or amount > 100_000_000:
        raise HTTPException(422, "Enter an amount between ₹1 lakh and ₹10 crore.")
    # Consent & exchange gate: a request is only passed to the bank's lending team if the customer has authorised sharing.
    from app.services import exchange as _ex
    if not _ex.has_consent(db, sid, "loan_application"):
        raise HTTPException(403, "Please allow 'Share my application with the bank's lending team' in Privacy & consent before sending a request.")
    from app.models.entities import new_id
    from app.services.audit import log_audit

    app = CreditApplication(id=new_id("APP"), sme_id=sid, requested_amount=amount, tenure_months=int(body.get("tenure_months") or 24), purpose=(body.get("purpose") or "Working capital")[:120], facility_type="Working capital loan",
                            bureau_score=s.bureau_score, status="submitted")
    db.add(app)
    log_audit(db, actor=f"{s.name} (portal)", role="SME Customer", action="credit_request_submitted", category="credit", entity_type="credit_application", entity_id=app.id, sme_id=sid,
              summary=f"{s.name} submitted a credit request for {inr(amount)} via the portal")
    db.commit()
    return {"id": app.id, "status": "Received", "message": "Your request has been received. Your relationship manager will be in touch."}


# =============================================================================== Customer Portal journey (two-sided architecture update)
# Every route below reads the customer data domain only. Writes require the SME Customer role (a bank user cannot grant consent
# or edit a submission on the customer's behalf). Payloads are validated by the customer-safe schemas and checked by exchange.customer_safe.
import threading
import time

from fastapi import File, Form, UploadFile

from app.api.deps import require_roles
from app.graphs.builder import TEMPLATES
from app.graphs.nodes import NODE_META
from app.models.entities import WorkflowRun
from app.schemas import customer as cs
from app.services import customer as cust
from app.services import exchange, readiness
from app.services import workflow_runner as wr

CUSTOMER_ONLY = require_roles("SME Customer")


def _sme(db: Session, sme_id: str | None) -> SME:
    s = db.get(SME, sme_id or _default_sme(db))
    if not s:
        raise HTTPException(404, "Company not found")
    return s


@router.get("/consent", response_model=cs.ConsentOut)
def get_consent(sme_id: str | None = None, db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    state = exchange.consent_state(db, s.id)
    purposes = [{"purpose": k, "label": v["label"], "description": v["description"], "direction": v["direction"], "shares": v["shares"], "not_shared": v["not_shared"],
                 "granted": state[k]["granted"], "updated_at": state[k]["updated_at"]} for k, v in exchange.PURPOSES.items()]
    return {"purposes": purposes, "history": exchange.consent_history(db, s.id)[:20], "note": "You can change these at any time. Withdrawing consent stops the bank from seeing your submitted information from that moment."}


@router.post("/consent", response_model=cs.ConsentOut)
def put_consent(body: dict, sme_id: str | None = None, ctx: Ctx = Depends(CUSTOMER_ONLY), db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    try:
        exchange.set_consent(db, s.id, str(body.get("purpose")), bool(body.get("granted")), cust.actor_for(s), "SME Customer", "via customer portal")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return get_consent(s.id, db)


@router.get("/submission")
def get_submission(sme_id: str | None = None, db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    return exchange.customer_safe({"submission": cust.submission_view(exchange.latest_submission(db, s.id), get_as_of_date()), "documents": cust.docs_summary(db, s.id),
                                   "prefill": {"business_name": s.name, "industry": s.industry, "city": s.city, "state": s.state, "legal_form": s.legal_form, "employees": s.employees}})


@router.post("/submission")
def save_submission(body: dict, sme_id: str | None = None, ctx: Ctx = Depends(CUSTOMER_ONLY), db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    try:
        row = cust.submit(db, s, body)
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return {"version": row.version, "message": "Saved. Run a readiness assessment to see how this changes your view."}


@router.post("/documents")
async def upload_document(sme_id: str | None = None, file: UploadFile = File(...), doc_type: str | None = Form(None), ctx: Ctx = Depends(CUSTOMER_ONLY), db: Session = Depends(get_db)):
    """Reuses the existing KYC ingestion. The customer only gets a plain receipt - extracted fields and KYC exceptions stay with the KYC team."""
    from app.ml import kyc as kyc_ml
    from app.services import kyc_service

    s = _sme(db, sme_id)
    app = kyc_service.get_or_create_application(db, s.id)
    if app.status in kyc_service.TERMINAL:
        raise HTTPException(409, "Your onboarding is already complete; documents can no longer be added here.")
    content = await file.read()
    if doc_type and doc_type not in kyc_ml.DOC_TYPES:
        raise HTTPException(400, "Unknown document type")
    chk = kyc_ml.validate_file(file.filename or "upload", content, file.content_type)
    if not chk.ok:
        raise HTTPException(400, chk.error)
    try:
        res = kyc_service.ingest_document(db, app.id, file.filename or "upload", content, file.content_type, doc_type, cust.actor_for(s), "SME Customer")
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    ok = res["status"] == "extracted"
    return {"doc_type": res["doc_type"], "label": kyc_ml.DOC_LABELS.get(res["doc_type"], res["doc_type"]), "received": True,
            "message": "Received." if ok else "Received, but we could not read it clearly. A clearer copy may be requested.", "documents": cust.docs_summary(db, s.id)}


@router.get("/financial-profile", response_model=cs.ProfileOut)
def financial_profile(sme_id: str | None = None, db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    x = cust.live_inputs(db, s.id, get_as_of_date())
    p = cust.build_profile(x["features"], x["forecast"], x["submission"])
    return exchange.customer_safe({**p, "submission_version": (x["submission"] or {}).get("version"),
                                   "note": "Worked out from your account statements and the figures you submitted. Synthetic demo data."})


def _readiness_envelope(db: Session, s: SME) -> dict:
    rows = list(db.scalars(select(cust.ReadinessAssessment).where(cust.ReadinessAssessment.sme_id == s.id).order_by(cust.ReadinessAssessment.version.desc()).limit(2)))
    consent = exchange.has_consent(db, s.id, "readiness_assessment")
    return {"latest": cust.readiness_view(rows[0], rows[1] if len(rows) > 1 else None) if rows else None, "history": cust.readiness_history(db, s.id), "consent_granted": consent,
            "can_assess": consent, "disclaimer": readiness.DISCLAIMER}


@router.get("/readiness", response_model=cs.ReadinessEnvelope)
def get_readiness(sme_id: str | None = None, db: Session = Depends(get_db)):
    return exchange.customer_safe(_readiness_envelope(db, _sme(db, sme_id)))


def _chain_improvement(run_id: str, sme_id: str, user: str) -> None:
    """After the readiness run finishes, refresh the improvement plan + market view (workflow 4)."""
    from app.db.session import session_scope

    for _ in range(600):
        with session_scope() as db:
            st = db.get(WorkflowRun, run_id).status
        if st == "completed":
            wr.start_run(sme_id, "customer_improvement", {"reassessment": True}, user, "SME Customer")
            return
        if st in ("failed", "cancelled"):
            return
        time.sleep(0.2)


@router.post("/readiness/run")
def run_readiness(body: dict | None = None, sme_id: str | None = None, ctx: Ctx = Depends(CUSTOMER_ONLY), db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    if not exchange.has_consent(db, s.id, "readiness_assessment"):
        raise HTTPException(403, "Please allow 'Use my information to estimate loan readiness' in Privacy & consent first.")
    first = cust.latest_readiness(db, s.id) is None
    user = cust.actor_for(s)
    run_id = wr.start_run(s.id, "customer_readiness", {"reassessment": not first}, user, "SME Customer")
    from app.services.audit import log_audit
    log_audit(db, actor=user, role="SME Customer", action="reassessment_requested" if not first else "readiness_requested", category="customer", entity_type="workflow_run", entity_id=run_id, sme_id=s.id, run_id=run_id,
              summary=f"{s.name} requested a {'fresh readiness reassessment' if not first else 'readiness assessment'}")
    db.commit()
    threading.Thread(target=_chain_improvement, args=(run_id, s.id, user), daemon=True).start()
    return {"run_id": run_id, "status": "started", "message": "Working on it. This uses your latest submitted information."}


@router.get("/runs/{run_id}")
def run_status(run_id: str, db: Session = Depends(get_db)):
    r = db.get(WorkflowRun, run_id)
    if not r or r.template not in ("customer_readiness", "customer_improvement"):
        raise HTTPException(404, "Run not found")
    done = [e["node"] for e in wr.events_after(run_id, 0) if e["event_type"] == "completed"]
    steps = [n for st in TEMPLATES[r.template]["steps"] for n in (st if isinstance(st, list) else [st])]
    return {"status": r.status, "total_steps": len(steps), "completed_steps": len(done), "current_step": NODE_META.get(r.current_node, {}).get("label") if r.current_node else None,
            "failed": r.status == "failed", "message": "Something went wrong. Please try again." if r.status == "failed" else None}


@router.get("/plan", response_model=cs.PlanOut)
def get_plan(sme_id: str | None = None, db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    cur = cust.latest_readiness(db, s.id)
    return exchange.customer_safe({**cust.plan_view(db, s.id), "based_on_assessment_version": cur.version if cur else None})


@router.post("/plan/{action_id}")
def set_plan_action(action_id: str, body: dict, sme_id: str | None = None, ctx: Ctx = Depends(CUSTOMER_ONLY), db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    try:
        cust.set_action_status(db, s, action_id, bool(body.get("done")))
    except LookupError:
        raise HTTPException(404, "Action not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return exchange.customer_safe(cust.plan_view(db, s.id))


@router.get("/growth", response_model=cs.GrowthOut)
def get_growth(sme_id: str | None = None, db: Session = Depends(get_db)):
    s = _sme(db, sme_id)
    if not exchange.has_consent(db, s.id, "market_intelligence"):
        return {"consent_required": True}
    snap = cust.latest_market(db, s.id)
    return exchange.customer_safe({"consent_required": False, "market": (snap or {}).get("market"), "growth": (snap or {}).get("growth"), "created_at": (snap or {}).get("created_at")})


@router.get("/activity")
def get_activity(sme_id: str | None = None, db: Session = Depends(get_db)):
    return {"events": cust.activity(db, _sme(db, sme_id).id)}
