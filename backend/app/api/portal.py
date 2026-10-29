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
def credit_request(body: dict, sme_id: str | None = None, db: Session = Depends(get_db)):
    sid = sme_id or _default_sme(db)
    s = db.get(SME, sid)
    amount = float(body.get("amount") or 0)
    if amount < 100_000 or amount > 100_000_000:
        raise HTTPException(422, "Enter an amount between ₹1 lakh and ₹10 crore.")
    from app.models.entities import new_id
    from app.services.audit import log_audit

    app = CreditApplication(id=new_id("APP"), sme_id=sid, requested_amount=amount, tenure_months=int(body.get("tenure_months") or 24), purpose=(body.get("purpose") or "Working capital")[:120], facility_type="Working capital loan",
                            bureau_score=s.bureau_score, status="submitted")
    db.add(app)
    log_audit(db, actor=f"{s.name} (portal)", role="SME Customer", action="credit_request_submitted", category="credit", entity_type="credit_application", entity_id=app.id, sme_id=sid,
              summary=f"{s.name} submitted a credit request for {inr(amount)} via the portal")
    db.commit()
    return {"id": app.id, "status": "Received", "message": "Your request has been received. Your relationship manager will be in touch."}
