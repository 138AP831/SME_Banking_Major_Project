"""Shared serialisation helpers for API responses (masking applied by role)."""
from __future__ import annotations

from typing import Any

from app.core.util import mask_account, mask_email, mask_gstin, mask_pan, mask_phone
from app.models.entities import SME


def sme_summary(s: SME) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "industry": s.industry, "city": s.city, "state": s.state, "segment": s.segment, "onboarding_status": s.onboarding_status,
            "assigned_rm": s.assigned_rm, "sanctioned_limit": s.sanctioned_limit, "outstanding_exposure": s.outstanding_exposure, "utilization": s.utilization,
            "current_balance": s.current_balance, "consent_status": s.consent_status, "products_held": s.products_held, "persona": s.persona, "employees": s.employees,
            "declared_annual_turnover": s.declared_annual_turnover, "data_source": s.data_source}


def sme_profile(s: SME, reveal: bool, role: str) -> dict[str, Any]:
    can_contact = role in ("Relationship Manager", "Credit Analyst", "KYC Analyst")
    return {**sme_summary(s), "legal_form": s.legal_form, "incorporation_date": s.incorporation_date.isoformat(), "director_name": s.director_name,
            "pan": s.pan if reveal else mask_pan(s.pan), "gstin": s.gstin if reveal else mask_gstin(s.gstin), "pii_revealed": reveal,
            "contact_email": s.contact_email if can_contact else mask_email(s.contact_email), "contact_phone": s.contact_phone if can_contact else mask_phone(s.contact_phone),
            "bureau_score": s.bureau_score, "bureau_note": "SYNTHETIC bureau score" if s.bureau_score else "No bureau score on file",
            "loan_emi_monthly": s.loan_emi_monthly, "utilization_history": s.utilization_history,
            "utilization_note": "Utilization history is SIMULATED (no live facility feed in this prototype)." if s.utilization_history else None,
            "last_activity_at": s.last_activity_at.isoformat(timespec="seconds")}
