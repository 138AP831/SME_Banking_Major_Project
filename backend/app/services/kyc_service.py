"""KYC application logic: ingestion, (re)evaluation, manual correction, serialisation."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.ml import kyc as kyc_ml
from app.models.entities import SME, CreditApplication, Document, HumanReviewDecision, KYCApplication, new_id, utcnow
from app.repositories import data as repo
from app.services.audit import log_audit

TERMINAL = ("approved", "rejected")
REVEAL_ROLES = {"KYC Analyst", "Credit Analyst"}


def timeline_add(app: KYCApplication, event: str, detail: str, actor: str = "SMEFlow AI") -> None:
    app.timeline = [*(app.timeline or []), {"ts": utcnow().isoformat(timespec="seconds"), "event": event, "detail": detail, "actor": actor}]
    app.updated_at = utcnow()


def get_or_create_application(db: Session, sme_id: str) -> KYCApplication:
    app = repo.latest_kyc(db, sme_id)
    if app:
        return app
    app = KYCApplication(id=new_id("KYC"), sme_id=sme_id, status="not_started", exceptions=[], cross_checks=[], missing_documents=list(kyc_ml.REQUIRED_DOCS), timeline=[],
                         external_verification=kyc_ml.simulated_external_verification(sme_id))
    timeline_add(app, "application_created", "KYC application opened")
    db.add(app)
    db.flush()
    return app


def kyc_summary_for_policy(db: Session, sme_id: str) -> dict[str, Any]:
    app = repo.latest_kyc(db, sme_id)
    if not app:
        return {"status": "not_started", "application_id": None, "exceptions": [], "missing_documents": list(kyc_ml.REQUIRED_DOCS)}
    return {"status": app.status, "application_id": app.id, "exceptions": app.exceptions or [], "missing_documents": app.missing_documents or []}


def evaluate_application(db: Session, app: KYCApplication, as_of: date | None = None, actor: str = "SMEFlow AI", keep_decided: bool = True, note: str | None = None) -> dict[str, Any]:
    """Re-run cross-document checks + status from the stored documents."""
    as_of = as_of or get_as_of_date()
    sme = db.get(SME, app.sme_id)
    store, results = repo.kyc_docs_store(db, app.id)
    credit_app = repo.open_credit_application(db, app.sme_id)
    sme_info = {"pan": sme.pan, "director_name": sme.director_name}
    checks = kyc_ml.cross_document_checks(store, sme_info, as_of, credit_app.requested_amount if credit_app else 0.0)
    st = kyc_ml.compute_application_status(results, checks)
    app.cross_checks = to_jsonable(checks)
    app.exceptions = to_jsonable(st["exceptions"])
    app.missing_documents = st["missing_documents"] if results else list(kyc_ml.REQUIRED_DOCS)
    if not app.external_verification:
        app.external_verification = kyc_ml.simulated_external_verification(sme.name)
    prev = app.status
    new = st["status"]
    if app.status in TERMINAL and keep_decided:
        new = app.status
    elif app.status == "info_requested" and new in ("exception", "in_progress") and keep_decided:
        new = "info_requested"
    app.status = new
    if new != prev:
        timeline_add(app, "status_changed", f"{prev} → {new}: {st['reason']}", actor)
    elif note:
        timeline_add(app, "re_evaluated", note, actor)
    return {**st, "computed_status": st["status"], "status": app.status, "previous_status": prev}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "document"


def ingest_document(db: Session, application_id: str, filename: str, content: bytes, mime: str | None, declared_type: str | None, actor: str, role: str,
                    store_file: bool = True, as_of: date | None = None) -> dict[str, Any]:
    app = db.get(KYCApplication, application_id)
    if not app:
        raise LookupError(application_id)
    as_of = as_of or get_as_of_date()
    processed = kyc_ml.process_document(filename, content, mime, as_of, declared_type)
    dtype = processed.doc_type or declared_type
    if dtype is None:
        raise ValueError(processed.error or "Could not determine the document type.")
    path = None
    if store_file and processed.sha256:
        d = Path(get_settings().upload_dir) / application_id
        d.mkdir(parents=True, exist_ok=True)
        path = str(d / f"{dtype}_{_safe_name(filename)}")
        Path(path).write_bytes(content)
    # one document per type per application: replace the previous version
    for old in db.scalars(select(Document).where(Document.application_id == application_id, Document.doc_type == dtype)):
        db.delete(old)
    db.flush()
    doc = Document(id=new_id("DOC"), application_id=application_id, sme_id=app.sme_id, doc_type=dtype, filename=_safe_name(filename), file_path=path, mime_type=mime or "application/octet-stream",
                   size_bytes=len(content), status=processed.status, extraction_method=processed.method, extracted_fields=to_jsonable(processed.fields), confidence=processed.confidence,
                   validation=to_jsonable(processed.validation), error=processed.error)
    db.add(doc)
    db.flush()
    sme = db.get(SME, app.sme_id)
    log_audit(db, actor=actor, role=role, action="document_uploaded", category="kyc", entity_type="document", entity_id=doc.id, sme_id=app.sme_id,
              summary=f"{kyc_ml.DOC_LABELS.get(dtype, dtype)} processed for {sme.name}: {processed.status} (confidence {processed.confidence:.0%})",
              details={"doc_type": dtype, "status": processed.status, "confidence": processed.confidence, "method": processed.method, "sha256": processed.sha256},
              severity="attention" if processed.status != "extracted" else "info", is_ai=True)
    timeline_add(app, "document_processed", f"{kyc_ml.DOC_LABELS.get(dtype, dtype)}: {processed.status} ({processed.confidence:.0%})", actor)
    result = evaluate_application(db, app, as_of, actor=actor, keep_decided=False)
    if app.status in ("exception",):
        log_audit(db, actor="SMEFlow AI", role="system", action="kyc_exception_detected", category="kyc", entity_type="kyc_application", entity_id=app.id, sme_id=app.sme_id,
                  summary=f"KYC exception detected for {sme.name}: {len(app.exceptions)} issue(s)", severity="attention", details={"codes": [e['code'] for e in app.exceptions]}, is_ai=True)
    return {"document_id": doc.id, "doc_type": dtype, "status": processed.status, "error": processed.error, "application_status": app.status, "evaluation": result}


def correct_document(db: Session, doc_id: str, corrections: dict[str, str], actor: str, role: str) -> dict[str, Any]:
    doc = db.get(Document, doc_id)
    if not doc:
        raise LookupError(doc_id)
    app = db.get(KYCApplication, doc.application_id)
    if app.status in TERMINAL:
        raise PermissionError(f"The application is '{app.status}' and can no longer be edited.")
    fields = dict(doc.extracted_fields or {})
    clean: dict[str, str] = {}
    for k, v in corrections.items():
        v = (v or "").strip()
        if k == "aadhaar_last4" or k == "aadhaar_number":
            digits = re.sub(r"\D", "", v)
            if len(digits) == 12:
                if not kyc_ml.verhoeff_valid(digits):
                    raise ValueError("The Aadhaar number failed the checksum validation. Check the digits.")
                v = digits[-4:]  # the full number is never stored
            elif len(digits) == 4:
                v = digits
            else:
                raise ValueError("Enter the 12-digit Aadhaar number (only the last 4 digits are stored) or the last 4 digits.")
            k = "aadhaar_last4"
        elif k in ("pan", "gstin", "ifsc"):
            v = v.replace(" ", "").upper()
        if k not in fields and k not in dict(kyc_ml._FIELD_SPECS.get(doc.doc_type, [])) and k != "aadhaar_last4":
            raise ValueError(f"'{k}' is not a field of {doc.doc_type}.")
        clean[k] = v
    # re-validate corrected values with the same format rules
    probe = {k: {"value": v, "confidence": 1.0, "found": bool(v)} for k, v in {**{fk: fv.get("value") for fk, fv in fields.items()}, **clean}.items() if k != "aadhaar_last4"}
    checks = kyc_ml.validate_formats(doc.doc_type, probe, get_as_of_date())
    failed = [c for c in checks if not c["passed"] and c["field"] in clean]
    if failed:
        raise ValueError("; ".join(c["message"] for c in failed))
    doc.manual_corrections = {**(doc.manual_corrections or {}), **clean}
    all_checks = [c for c in checks if c["passed"] or c["field"] not in clean]
    doc.validation = to_jsonable([c for c in all_checks if not (c["code"] == "FIELD_MISSING" and c["field"] in clean)])
    doc.status = "corrected" if not [c for c in doc.validation if not c["passed"]] else doc.status
    doc.confidence = 1.0 if doc.status == "corrected" else doc.confidence
    doc.error = None
    log_audit(db, actor=actor, role=role, action="document_corrected", category="kyc", entity_type="document", entity_id=doc.id, sme_id=doc.sme_id,
              summary=f"Manual correction applied to {kyc_ml.DOC_LABELS.get(doc.doc_type, doc.doc_type)} ({', '.join(clean)})", details={"fields": list(clean)})
    timeline_add(app, "document_corrected", f"{kyc_ml.DOC_LABELS.get(doc.doc_type, doc.doc_type)}: {', '.join(clean)} corrected manually", actor)
    return evaluate_application(db, app, actor=actor, keep_decided=False)


def create_demo_application_if_missing(db: Session, sme_id: str) -> KYCApplication:
    return get_or_create_application(db, sme_id)


# ----------------------------------------------------------------------------- serialisation
def _field_view(fields: dict, reveal: bool) -> dict:
    out = kyc_ml.mask_fields_for_role(fields, reveal)
    return {k: {"value": v.get("value"), "confidence": v.get("confidence"), "valid": v.get("valid"), "sensitive": bool(v.get("sensitive")), "masked": bool(v.get("masked")),
                "corrected": bool(v.get("corrected"))} for k, v in out.items()}


def application_view(db: Session, app: KYCApplication, role: str = "Relationship Manager", reveal: bool = False) -> dict[str, Any]:
    can_reveal = reveal and role in REVEAL_ROLES
    sme = db.get(SME, app.sme_id)
    docs = list(db.scalars(select(Document).where(Document.application_id == app.id).order_by(Document.uploaded_at)))
    pending = db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.status == "pending", HumanReviewDecision.sme_id == app.sme_id,
                                                           HumanReviewDecision.review_kind == "kyc_exception")).first()
    out_docs = []
    for d in docs:
        fields = dict(d.extracted_fields or {})
        for k, v in (d.manual_corrections or {}).items():
            fields[k] = {**fields.get(k, {}), "value": v, "confidence": 1.0, "found": True, "corrected": True, "sensitive": k in kyc_ml.SENSITIVE_FIELDS}
        out_docs.append({"id": d.id, "doc_type": d.doc_type, "label": kyc_ml.DOC_LABELS.get(d.doc_type, d.doc_type), "filename": d.filename, "status": d.status,
                         "confidence": d.confidence, "method": d.extraction_method, "fields": _field_view(fields, can_reveal), "validation": d.validation or [],
                         "error": d.error, "uploaded_at": d.uploaded_at.isoformat(timespec="seconds"), "has_file": bool(d.file_path), "size_bytes": d.size_bytes,
                         "manual_corrections": sorted((d.manual_corrections or {}).keys())})
    present = {d["doc_type"] for d in out_docs}
    return {
        "id": app.id, "sme_id": app.sme_id, "sme_name": sme.name, "industry": sme.industry, "status": app.status, "exceptions": app.exceptions or [], "cross_checks": app.cross_checks or [],
        "missing_documents": app.missing_documents or [], "required_documents": [{"type": t, "label": kyc_ml.DOC_LABELS[t], "present": t in present} for t in kyc_ml.REQUIRED_DOCS],
        "documents": out_docs, "timeline": app.timeline or [], "external_verification": app.external_verification or kyc_ml.simulated_external_verification(sme.name),
        "created_at": app.created_at.isoformat(timespec="seconds"), "updated_at": app.updated_at.isoformat(timespec="seconds"),
        "pending_review": ({"id": pending.id, "run_id": pending.run_id, "reasons": pending.trigger_reasons, "allowed_roles": pending.allowed_roles} if pending else None),
        "pii_revealed": can_reveal, "reveal_denied": bool(reveal and not can_reveal),
        "ocr_available": kyc_ml.ocr_available(), "doc_types": [{"type": t, "label": kyc_ml.DOC_LABELS[t]} for t in kyc_ml.DOC_TYPES],
    }


def list_view(db: Session) -> list[dict]:
    rows = db.execute(select(KYCApplication, SME).join(SME, SME.id == KYCApplication.sme_id).order_by(KYCApplication.updated_at.desc())).all()
    out = []
    for app, sme in rows:
        ndocs = db.scalar(select(__import__("sqlalchemy").func.count(Document.id)).where(Document.application_id == app.id)) or 0
        out.append({"id": app.id, "sme_id": sme.id, "sme_name": sme.name, "industry": sme.industry, "status": app.status, "documents": ndocs, "missing": len(app.missing_documents or []),
                    "exceptions": len(app.exceptions or []), "blocking": len([e for e in (app.exceptions or []) if e.get("severity") == "blocking"]),
                    "assigned_rm": sme.assigned_rm, "updated_at": app.updated_at.isoformat(timespec="seconds"), "onboarding_status": sme.onboarding_status})
    return out
