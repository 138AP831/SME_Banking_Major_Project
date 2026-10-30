"""Consent & Exchange Gate - the only sanctioned path between the customer data domain and the bank side.

* customer -> bank: a submission is visible to bank-side users/workflows only while the matching purpose consent is granted,
  and only the whitelisted fields are released. Every grant/denial of access is audited.
* bank -> customer: the customer side never receives bank-restricted data. Customer-facing graphs and APIs are built from
  ``customer_sme_view`` (an allow-list) and responses are checked with ``find_forbidden`` (a deny-list used in tests and guards).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.util import to_jsonable
from app.models.entities import ConsentRecord, CustomerSubmission, new_id
from app.services.audit import log_audit

PURPOSES: dict[str, dict[str, Any]] = {
    "loan_application": {
        "label": "Share my application with the bank's lending team",
        "description": "Lets the bank's credit team use the business details, financial information and loan request you submit here to review a loan application.",
        "direction": "customer_to_bank",
        "shares": ["Business profile", "Financial summary you entered", "Existing loans you listed", "Requested amount, tenure and purpose", "Business location"],
        "not_shared": "Your documents stay in KYC onboarding; PAN, GSTIN and Aadhaar are never included in this share.",
    },
    "readiness_assessment": {
        "label": "Use my information to estimate loan readiness",
        "description": "Lets SMEFlow analyse your statements and submission to give you an indicative readiness view and an improvement plan. This stays on your side; it is not sent to the bank.",
        "direction": "customer_only",
        "shares": [],
        "not_shared": "Nothing is shared with the bank for this purpose.",
    },
    "market_intelligence": {
        "label": "Look up market information for my location",
        "description": "Lets SMEFlow use your business category and city to look up nearby businesses and market signals. Only the city and business category are sent to an external service.",
        "direction": "customer_only",
        "shares": [],
        "not_shared": "No financial data is sent to the external service.",
    },
}

STAFF_ROLES = ("Relationship Manager", "Credit Analyst", "KYC Analyst", "Risk Manager", "system")
# Fields a customer-side workflow may see on the SME master record. Everything else (bureau score, exposure, utilisation,
# facility limits, EMI on bank loan accounts, assigned RM notes, PAN/GSTIN, ...) is bank-restricted and is dropped.
CUSTOMER_SME_FIELDS = ("id", "name", "industry", "legal_form", "city", "state", "incorporation_date", "employees", "declared_annual_turnover", "current_balance", "products_held")
SHARED_SUBMISSION_FIELDS = ("version", "business_profile", "financials", "existing_loans", "requested_amount", "tenure_months", "purpose", "location", "created_at")

FORBIDDEN_CUSTOMER_KEYS = frozenset({
    "pd_estimate", "model_score", "risk_category", "policy_outcome", "policy_rules", "bureau_score", "priority_score", "priority_level", "risk_factors", "positive_factors",
    "talking_points", "recommended_action", "escalation", "escalations", "rm_feedback", "rm_notes", "commitment_amount", "commitment_status", "issue_verified", "outstanding_exposure",
    "utilization", "utilization_history", "sanctioned_limit", "exposure", "alert", "alerts", "human_review", "review_kind", "trigger_reasons",
})


class ConsentRequired(Exception):
    def __init__(self, purpose: str):
        super().__init__(f"Consent for '{purpose}' has not been granted by the customer.")
        self.purpose = purpose


# --------------------------------------------------------------------------- consent
def consent_state(db: Session, sme_id: str) -> dict[str, dict[str, Any]]:
    """Current consent per purpose (latest record wins)."""
    out: dict[str, dict[str, Any]] = {p: {"purpose": p, "granted": False, "updated_at": None, "updated_by": None} for p in PURPOSES}
    for r in db.scalars(select(ConsentRecord).where(ConsentRecord.sme_id == sme_id).order_by(ConsentRecord.created_at, ConsentRecord.id)):
        if r.purpose in out:
            out[r.purpose] = {"purpose": r.purpose, "granted": r.granted, "updated_at": r.created_at.isoformat(timespec="seconds"), "updated_by": r.actor}
    return out


def has_consent(db: Session, sme_id: str, purpose: str) -> bool:
    return bool(consent_state(db, sme_id).get(purpose, {}).get("granted"))


def require_consent(db: Session, sme_id: str, purpose: str) -> None:
    if not has_consent(db, sme_id, purpose):
        raise ConsentRequired(purpose)


def set_consent(db: Session, sme_id: str, purpose: str, granted: bool, actor: str, role: str, note: str = "") -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown consent purpose '{purpose}'")
    row = ConsentRecord(id=new_id("CON"), sme_id=sme_id, purpose=purpose, granted=granted, actor=actor, role=role, note=note[:300])
    db.add(row)
    db.flush()
    log_audit(db, actor=actor, role=role, action="consent_granted" if granted else "consent_withdrawn", category="consent", entity_type="consent", entity_id=row.id, sme_id=sme_id,
              summary=f"Consent for '{PURPOSES[purpose]['label']}' {'granted' if granted else 'withdrawn'}", details={"purpose": purpose, "granted": granted}, severity="info" if granted else "attention")
    return {"purpose": purpose, "granted": granted, "updated_at": row.created_at.isoformat(timespec="seconds"), "updated_by": actor}


def consent_history(db: Session, sme_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(ConsentRecord).where(ConsentRecord.sme_id == sme_id).order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc()))
    return [{"id": r.id, "purpose": r.purpose, "label": PURPOSES.get(r.purpose, {}).get("label", r.purpose), "granted": r.granted, "by": r.actor, "at": r.created_at.isoformat(timespec="seconds")} for r in rows]


# --------------------------------------------------------------------------- customer -> bank
def latest_submission(db: Session, sme_id: str) -> CustomerSubmission | None:
    return db.scalars(select(CustomerSubmission).where(CustomerSubmission.sme_id == sme_id).order_by(CustomerSubmission.version.desc())).first()


def submission_dict(s: CustomerSubmission) -> dict[str, Any]:
    return to_jsonable({"id": s.id, "version": s.version, "business_profile": s.business_profile, "financials": s.financials, "existing_loans": s.existing_loans, "requested_amount": s.requested_amount,
                        "tenure_months": s.tenure_months, "purpose": s.purpose, "location": s.location, "created_at": s.created_at.isoformat(timespec="seconds")})


def bank_view_of_submission(db: Session, sme_id: str, actor: str, role: str, purpose: str = "loan_application") -> dict[str, Any]:
    """The gate. Returns the customer's submission only if the role is bank-side AND consent for the purpose is granted. Always audited."""
    if role not in STAFF_ROLES:
        raise PermissionError("Only bank-side roles can use the exchange gate to read customer submissions.")
    granted = has_consent(db, sme_id, purpose)
    sub = latest_submission(db, sme_id) if granted else None
    shared = {k: v for k, v in (submission_dict(sub) if sub else {}).items() if k in SHARED_SUBMISSION_FIELDS}
    log_audit(db, actor=actor, role=role, action="exchange_access_granted" if granted else "exchange_access_denied", category="consent", entity_type="exchange_gate", entity_id=sme_id, sme_id=sme_id,
              summary=("Bank-side read of customer submission released through the exchange gate" if granted else "Bank-side read of customer submission blocked: customer consent not granted") + f" (purpose: {purpose})",
              details={"purpose": purpose, "fields_released": sorted(shared) if granted else [], "has_submission": bool(sub)}, severity="info" if granted else "attention")
    return {"authorised": granted, "purpose": purpose, "reason": None if granted else "The customer has not authorised sharing their submitted information for this purpose.",
            "shared_fields": sorted(shared), "submission": shared or None}


# --------------------------------------------------------------------------- bank -> customer boundary
def customer_sme_view(sme: dict[str, Any]) -> dict[str, Any]:
    """Allow-list projection of the SME master record for customer-side graph state."""
    return to_jsonable({k: sme.get(k) for k in CUSTOMER_SME_FIELDS})


def find_forbidden(obj: Any, path: str = "") -> list[str]:
    """Paths of any bank-restricted key found in a customer-facing payload (used by guards and tests)."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k) in FORBIDDEN_CUSTOMER_KEYS:
                found.append(p)
            found += find_forbidden(v, p)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found += find_forbidden(v, f"{path}[{i}]")
    return found


def customer_safe(payload: Any) -> Any:
    """Runtime guard for customer endpoints: refuses to return a payload that contains a bank-restricted key."""
    bad = find_forbidden(payload)
    if bad:
        raise RuntimeError(f"Customer-facing payload contained bank-restricted field(s): {', '.join(bad[:5])}")
    return payload
