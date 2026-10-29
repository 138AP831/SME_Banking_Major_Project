"""Read helpers shared by services, API routes and LangGraph nodes."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (SME, Alert, CashflowForecast, CreditApplication, CreditAssessment, Document, KYCApplication,
                                 ProductRecommendation, RiskSignal, Transaction, WorkflowRun)


def sme_to_dict(s: SME) -> dict[str, Any]:
    return {c.name: getattr(s, c.name) for c in s.__table__.columns}


def get_sme_dict(db: Session, sme_id: str) -> dict[str, Any] | None:
    s = db.get(SME, sme_id)
    return sme_to_dict(s) if s else None


def load_txns(db: Session, sme_id: str, as_of: date | None = None, days: int | None = None) -> list[dict[str, Any]]:
    q = select(Transaction).where(Transaction.sme_id == sme_id)
    if as_of:
        q = q.where(Transaction.txn_date <= as_of)
        if days:
            q = q.where(Transaction.txn_date > as_of - timedelta(days=days))
    q = q.order_by(Transaction.txn_date, Transaction.id)
    return [dict(txn_date=t.txn_date, direction=t.direction, amount=t.amount, category=t.category, counterparty=t.counterparty,
                 balance_after=t.balance_after, is_bounced=t.is_bounced, days_delay=t.days_delay, id=t.id) for t in db.scalars(q)]


def latest_kyc(db: Session, sme_id: str) -> KYCApplication | None:
    return db.scalars(select(KYCApplication).where(KYCApplication.sme_id == sme_id).order_by(KYCApplication.created_at.desc())).first()


def open_credit_application(db: Session, sme_id: str) -> CreditApplication | None:
    return db.scalars(select(CreditApplication).where(CreditApplication.sme_id == sme_id,
                                                      CreditApplication.status.in_(["submitted", "under_review", "referred", "info_requested"]))
                      .order_by(CreditApplication.submitted_at.desc())).first()


def latest_assessment(db: Session, sme_id: str) -> CreditAssessment | None:
    return db.scalars(select(CreditAssessment).where(CreditAssessment.sme_id == sme_id).order_by(CreditAssessment.created_at.desc())).first()


def latest_forecast(db: Session, sme_id: str, baseline_only: bool = True) -> CashflowForecast | None:
    q = select(CashflowForecast).where(CashflowForecast.sme_id == sme_id)
    if baseline_only:
        q = q.where(CashflowForecast.is_baseline == True)  # noqa: E712
    return db.scalars(q.order_by(CashflowForecast.created_at.desc())).first()


def latest_recommendation(db: Session, sme_id: str) -> ProductRecommendation | None:
    return db.scalars(select(ProductRecommendation).where(ProductRecommendation.sme_id == sme_id).order_by(ProductRecommendation.created_at.desc())).first()


def active_alert(db: Session, sme_id: str) -> Alert | None:
    return db.scalars(select(Alert).where(Alert.sme_id == sme_id, Alert.review_status.in_(["open", "in_review", "escalated"])).order_by(Alert.created_at.desc())).first()


def latest_signals(db: Session, sme_id: str) -> list[RiskSignal]:
    last = db.scalars(select(RiskSignal).where(RiskSignal.sme_id == sme_id).order_by(RiskSignal.detected_at.desc())).first()
    if not last:
        return []
    return list(db.scalars(select(RiskSignal).where(RiskSignal.sme_id == sme_id, RiskSignal.run_id == last.run_id, RiskSignal.detected_at == last.detected_at)
                           .order_by(RiskSignal.severity.desc())))


def kyc_docs_store(db: Session, application_id: str) -> tuple[dict[str, dict], list[dict]]:
    """Docs of an application as ({doc_type: {fields,status}}, [doc_result dicts])."""
    docs = list(db.scalars(select(Document).where(Document.application_id == application_id).order_by(Document.uploaded_at)))
    store: dict[str, dict] = {}
    results: list[dict] = []
    for d in docs:
        fields = dict(d.extracted_fields or {})
        # manual corrections override extracted values
        for k, v in (d.manual_corrections or {}).items():
            if k in fields:
                fields[k] = {**fields[k], "value": v, "confidence": 1.0, "corrected": True}
            else:
                fields[k] = {"value": v, "confidence": 1.0, "found": True, "corrected": True}
        store[d.doc_type] = {"fields": fields, "status": d.status, "id": d.id}
        results.append({"doc_type": d.doc_type, "status": "extracted" if (d.manual_corrections and d.status == "corrected") else d.status,
                        "confidence": 1.0 if d.status == "corrected" else d.confidence, "validation": d.validation or [], "error": d.error, "id": d.id})
    return store, results
