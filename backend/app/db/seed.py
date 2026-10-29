"""Idempotent seed + reset. Everything here is SYNTHETIC. Documents are processed through the real KYC pipeline;
bootstrap workflows are real LangGraph runs (not canned results)."""
from __future__ import annotations

import copy
import shutil
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.core.clock import get_as_of_date
from app.db import synthetic as syn
from app.db.session import SessionLocal, drop_all, init_db, session_scope
from app.ml import credit as credit_ml
from app.ml import kyc as kyc_ml
from app.ml import kyc_docs
from app.ml import propensity as prop_ml
from app.ml.features import build_features
from app.models.entities import (SME, AppSetting, CreditApplication, KYCApplication, Transaction, User, new_id, utcnow)
from app.repositories import data as repo
from app.services import analysis
from app.services import kyc_service
from app.services import policy as policy_svc
from app.services.audit import log_audit

ROLES = ["Relationship Manager", "Credit Analyst", "KYC Analyst", "Risk Manager", "SME Customer"]
USERS = [("u-rm", "Alex Morgan", "Relationship Manager"), ("u-ca", "Dev Krishnan", "Credit Analyst"), ("u-kyc", "Isha Rao", "KYC Analyst"),
         ("u-risk", "Marcus Fernandes", "Risk Manager"), ("u-sme", "Ananya Digital Studio (customer)", "SME Customer")]
APPLICATIONS = {  # sme_id: (amount, tenure, purpose, facility)
    "SME-1001": (5_000_000, 36, "Capacity expansion - CNC line", "Term loan"),
    "SME-1003": (2_000_000, 24, "Working capital for new enterprise contracts", "Working capital loan"),
    "SME-1005": (4_000_000, 24, "Limit enhancement to bridge receivable delays", "Working capital loan"),
    "SME-1006": (3_000_000, 18, "Working capital against a large packaging order", "Working capital loan"),
    "SME-1004": (6_000_000, 36, "Warehouse and cold-storage build-out", "Term loan"),
    "SME-1007": (4_000_000, 36, "Machinery purchase", "Term loan"),
    "SME-1013": (3_000_000, 24, "Inventory financing", "Working capital loan"),
    "SME-1017": (4_500_000, 36, "Tooling upgrade", "Term loan"),
    "SME-1022": (3_500_000, 24, "Fleet refinancing", "Working capital loan"),
}
SCENARIO_SMES = {"SME-1001", "SME-1004", "SME-1006"}
CONSENT = {"SME-1035": "pending", "SME-1037": "withdrawn"}
L = 100_000.0


def _roster() -> list[dict]:
    out = []
    for r in copy.deepcopy(syn.ROSTER):
        n = int(r["id"].split("-")[1])
        if r["segment"] == "client" and r["status"] == "active" and "sanctioned" not in r:
            r["sanctioned"] = round(r["rev"] * 0.28 / (5 * L)) * 5 * L
            if n % 2 == 0 and r["arch"] != "growing":
                r["emi"] = round(r["rev"] * 0.03 / 1000) * 1000
                r["term_out"] = r["emi"] * 30
            else:
                r["emi"], r["term_out"] = 0.0, 0.0
        out.append(r)
    return out


def is_seeded() -> bool:
    with session_scope() as s:
        return (s.scalar(select(func.count(SME.id))) or 0) > 0


def seed_universe(as_of: date | None = None) -> dict:
    cfg = get_settings()
    as_of = as_of or get_as_of_date()
    init_db()
    ro = _roster()
    with session_scope() as s:
        s.merge(AppSetting(key="as_of_date", value=as_of.isoformat()))
        s.merge(AppSetting(key="credit_model_available", value=True))
        s.merge(AppSetting(key="seed_info", value={"seeded_at": utcnow().isoformat(timespec="seconds"), "rng_seed": cfg.seed_rng, "smes": len(ro), "data": "SYNTHETIC"}))
        for uid, name, role in USERS:
            s.merge(User(id=uid, name=name, role=role, email=f"{uid}@demo.smeflow.example"))
        s.merge(AppSetting(key="portal_sme_id", value="SME-1003"))
        for r in ro:
            ident = syn.identity_for(r, cfg.seed_rng)
            gen = syn.generate_transactions(r, as_of, cfg.seed_rng)
            active = r["segment"] == "client" and r["status"] == "active"
            sanctioned = float(r.get("sanctioned", 0.0))
            util = float(r.get("util_end", 0.0)) if sanctioned else 0.0
            term_out = float(r.get("term_out", 0.0))
            sme = SME(id=r["id"], name=r["name"], industry=r["industry"], legal_form=r["form"], city=r["city"], state=syn.CITY[r["city"]][0], incorporation_date=ident.incorporation_date,
                      employees=r["employees"], declared_annual_turnover=round(r["rev"] * 12 * float(r.get("declared_factor", 1.0)), 0), segment=r["segment"], onboarding_status=r["status"],
                      assigned_rm=r["rm"], director_name=ident.director_name, pan=ident.pan, gstin=ident.gstin,
                      contact_email=f"accounts@{''.join(c for c in r['name'].lower() if c.isalnum())[:18]}.example", contact_phone=f"+91 9{abs(hash(r['id'])) % 10**9:09d}",
                      sanctioned_limit=sanctioned, outstanding_exposure=round(sanctioned * util + term_out, 0), utilization=util, utilization_history=gen.utilization_history if active else [],
                      loan_emi_monthly=float(gen.params.get("emi", 0.0)) if active else 0.0, bureau_score=r.get("bureau"), current_balance=gen.current_balance,
                      products_held=r.get("products", ["Current account"] if r["segment"] == "client" else []), consent_status=CONSENT.get(r["id"], "granted"),
                      data_source="account_aggregator_simulated" if r["segment"] == "prospect" else "core_banking_synthetic", persona=r.get("persona"),
                      last_activity_at=utcnow() - timedelta(days=int(abs(hash(r["id"])) % 5)))
            s.add(sme)
            s.flush()
            s.bulk_insert_mappings(Transaction, [{**t, "sme_id": r["id"]} for t in gen.txns])
        log_audit(s, actor="system", role="system", action="data_seeded", category="system", entity_type="system", entity_id="seed", summary=f"Synthetic universe seeded: {len(ro)} SMEs (as-of {as_of})",
                  details={"as_of": as_of.isoformat(), "seed": cfg.seed_rng})
    return {"smes": len(ro), "as_of": as_of.isoformat()}


def seed_applications(as_of: date) -> None:
    cfg = get_settings()
    ro = {r["id"]: r for r in _roster()}
    with session_scope() as s:
        for sid, (amt, ten, purpose, fac) in APPLICATIONS.items():
            sme = s.get(SME, sid)
            s.add(CreditApplication(id=new_id("APP"), sme_id=sid, requested_amount=float(amt), tenure_months=ten, purpose=purpose, facility_type=fac, bureau_score=sme.bureau_score,
                                    status="submitted", submitted_at=utcnow() - timedelta(days=3)))
        # KYC: onboarding cases go through the real pipeline; existing clients carry a migrated, approved record
        for sme in s.scalars(select(SME).where(SME.segment == "client")):
            app = KYCApplication(id=new_id("KYC"), sme_id=sme.id, status="not_started", exceptions=[], cross_checks=[], missing_documents=list(kyc_ml.REQUIRED_DOCS), timeline=[],
                                 external_verification=kyc_ml.simulated_external_verification(sme.name))
            s.add(app)
            if sme.onboarding_status == "active":
                app.status, app.missing_documents = "approved", []
                kyc_service.timeline_add(app, "migrated", "KYC completed at account opening (synthetic legacy record; documents not stored in this prototype)", "core banking (synthetic)")
            else:
                kyc_service.timeline_add(app, "application_created", "Onboarding KYC application opened")
        s.flush()
        for sid, plan in kyc_docs.ONBOARDING_PLAN.items():
            r = ro[sid]
            ident = syn.identity_for(r, cfg.seed_rng)
            app = repo.latest_kyc(s, sid)
            for dt in plan["docs"]:
                fname, content, mime = kyc_docs.build_document(dt, r, ident, as_of, plan["flaws"], "pdf")
                kyc_service.ingest_document(s, app.id, fname, content, mime, dt, "Onboarding portal (synthetic)", "system", as_of=as_of)
            s.flush()
        a9 = repo.latest_kyc(s, "SME-1009")
        if a9 and a9.status != "approved":
            a9.status = "info_requested"
            kyc_service.timeline_add(a9, "info_requested", "Analyst requested a recent address proof (utility bill is stale)", "Isha Rao (KYC Analyst)")


def seed_analyses(as_of: date) -> None:
    """Bulk module outputs for the portfolio (same compute functions the graph uses)."""
    with session_scope() as s:
        ids = [r[0] for r in s.execute(select(SME.id)).all()]
    for sid in ids:
        with session_scope() as s:
            sme = repo.get_sme_dict(s, sid)
            txns = repo.load_txns(s, sid, as_of)
            base = build_features(sme, txns, as_of)
            app = repo.open_credit_application(s, sid)
            req = analysis.default_request(base, app, None)
            feats = build_features(sme, txns, as_of, req)
            rec = prop_ml.recommend(feats, sme)
            analysis.persist_recommendation(s, sme, None, rec)
            if sid in SCENARIO_SMES or sme["segment"] != "client" or sme["onboarding_status"] != "active":
                continue
            credit = analysis.compute_credit(sme, feats, req)
            credit["features_used"] = {k: feats.get(k) for k in credit_ml.CREDIT_FEATURES}
            fc = analysis.compute_forecast(sme, txns, as_of, 60, None)
            kyc = kyc_service.kyc_summary_for_policy(s, sid)
            pol = policy_svc.evaluate(workflow_type="credit_assessment", sme=sme, features=feats, credit=credit, kyc=kyc, forecast=fc, risk=None)
            credit["limit_low"], credit["limit_high"] = credit_ml.suggested_limit(feats, credit["risk_category"], pol["outcome"])
            text = credit_ml.narrative(sme["name"], credit, {"outcome": pol["outcome"], "triggered": pol["triggered"]}, req["requested_amount"])
            analysis.persist_credit(s, sme, None, credit, pol, text, None)
            analysis.persist_forecast(s, sme, None, fc, log=False)
            risk = analysis.compute_risk(sme, feats, fc, credit, [])
            analysis.persist_risk(s, sme, None, risk, None)


BOOTSTRAP = [("SME-1003", "full_lifecycle"), ("SME-1005", "full_lifecycle"), ("SME-1007", "kyc_onboarding"), ("SME-1010", "kyc_onboarding"), ("SME-1002", "cashflow_ews")]


def bootstrap_workflows() -> list[str]:
    from app.services import workflow_runner as wr

    cfg = get_settings()
    delay, cfg.demo_step_delay_ms = cfg.demo_step_delay_ms, 0
    try:
        return [wr.run_blocking(sid, tpl, {}, triggered_by="Bootstrap (seed)") for sid, tpl in BOOTSTRAP]
    finally:
        cfg.demo_step_delay_ms = delay


def ensure_models() -> None:
    credit_ml.load_model(train_if_missing=True)
    prop_ml.load()


def seed_all(with_workflows: bool | None = None) -> dict:
    cfg = get_settings()
    ensure_models()
    info = seed_universe()
    as_of = date.fromisoformat(info["as_of"])
    seed_applications(as_of)
    seed_analyses(as_of)
    runs = bootstrap_workflows() if (cfg.bootstrap_workflows if with_workflows is None else with_workflows) else []
    return {**info, "bootstrap_runs": runs}


def reset_all(with_workflows: bool | None = None) -> dict:
    from app.services import workflow_runner as wr

    wr.reset_checkpoints()
    drop_all()
    up = Path(get_settings().upload_dir)
    if up.exists():
        shutil.rmtree(up, ignore_errors=True)
    up.mkdir(parents=True, exist_ok=True)
    credit_ml.reset_model_cache()
    prop_ml.reset_cache()
    return seed_all(with_workflows)


def ensure_seeded() -> None:
    init_db()
    if not is_seeded():
        seed_all()


if __name__ == "__main__":
    import json

    print(json.dumps(reset_all(), indent=2, default=str))
