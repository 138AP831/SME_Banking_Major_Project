"""SQLAlchemy ORM entities - the shared SME data backbone.

Every module (prospecting, KYC, credit, cash-flow, EWS, workflows, audit) hangs off the
stable ``sme_id`` so the same customer is consistent everywhere.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


# --------------------------------------------------------------------------- users / settings
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40), index=True)
    email: Mapped[str] = mapped_column(String(120))


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


# --------------------------------------------------------------------------- SME core
class SME(Base):
    __tablename__ = "smes"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # SME-1001
    name: Mapped[str] = mapped_column(String(160), index=True)
    industry: Mapped[str] = mapped_column(String(60), index=True)
    legal_form: Mapped[str] = mapped_column(String(40))
    city: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(60))
    incorporation_date: Mapped[date] = mapped_column(Date)
    employees: Mapped[int] = mapped_column(Integer)
    declared_annual_turnover: Mapped[float] = mapped_column(Float)
    segment: Mapped[str] = mapped_column(String(16), index=True)  # client | prospect
    onboarding_status: Mapped[str] = mapped_column(String(24), index=True)  # active | onboarding | prospect
    assigned_rm: Mapped[str] = mapped_column(String(60), index=True)
    director_name: Mapped[str] = mapped_column(String(100))
    pan: Mapped[str] = mapped_column(String(12))
    gstin: Mapped[str] = mapped_column(String(20))
    contact_email: Mapped[str] = mapped_column(String(120))
    contact_phone: Mapped[str] = mapped_column(String(20))
    # facility / exposure
    sanctioned_limit: Mapped[float] = mapped_column(Float, default=0.0)
    outstanding_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    utilization: Mapped[float] = mapped_column(Float, default=0.0)
    utilization_history: Mapped[list] = mapped_column(JSON, default=list)  # SIMULATED month-end values
    loan_emi_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    bureau_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # SYNTHETIC
    current_balance: Mapped[float] = mapped_column(Float, default=0.0)
    products_held: Mapped[list] = mapped_column(JSON, default=list)
    consent_status: Mapped[str] = mapped_column(String(20), default="granted")  # granted | pending | withdrawn
    data_source: Mapped[str] = mapped_column(String(40), default="core_banking_synthetic")
    persona: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="sme", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    txn_date: Mapped[date] = mapped_column(Date, index=True)
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    amount: Mapped[float] = mapped_column(Float)  # always positive
    category: Mapped[str] = mapped_column(String(32), index=True)
    counterparty: Mapped[str] = mapped_column(String(100))
    balance_after: Mapped[float] = mapped_column(Float)
    is_bounced: Mapped[bool] = mapped_column(Boolean, default=False)
    days_delay: Mapped[int] = mapped_column(Integer, default=0)  # receipt delay vs due date / repayment delay
    sme: Mapped[SME] = relationship(back_populates="transactions")

    __table_args__ = (Index("ix_txn_sme_date", "sme_id", "txn_date"),)


# --------------------------------------------------------------------------- KYC
class KYCApplication(Base):
    __tablename__ = "kyc_applications"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    # not_started | in_progress | pending_review | exception | info_requested | approved | rejected
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    cross_checks: Mapped[list] = mapped_column(JSON, default=list)
    external_verification: Mapped[dict] = mapped_column(JSON, default=dict)  # SIMULATED
    missing_documents: Mapped[list] = mapped_column(JSON, default=list)
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    documents: Mapped[list["Document"]] = relationship(back_populates="application", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("kyc_applications.id"), index=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(200))
    file_path: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(60), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24))  # extracted | low_confidence | failed | corrected
    extraction_method: Mapped[str] = mapped_column(String(40), default="rule_based_text")
    extracted_fields: Mapped[dict] = mapped_column(JSON, default=dict)  # field -> {value, confidence, valid, note}
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    validation: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    manual_corrections: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[KYCApplication] = relationship(back_populates="documents")


# --------------------------------------------------------------------------- Credit
class CreditApplication(Base):
    __tablename__ = "credit_applications"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    requested_amount: Mapped[float] = mapped_column(Float)
    tenure_months: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(120))
    facility_type: Mapped[str] = mapped_column(String(40), default="Working capital loan")
    bureau_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(28), index=True)
    # draft | submitted | under_review | referred | recommended_to_proceed | declined_by_reviewer | info_requested | not_eligible
    latest_assessment_id: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CreditAssessment(Base):
    __tablename__ = "credit_assessments"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    application_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    requested_amount: Mapped[float] = mapped_column(Float)
    tenure_months: Mapped[int] = mapped_column(Integer)
    model_score: Mapped[int] = mapped_column(Integer)  # 300-900, higher = better
    pd_estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # synthetic-trained, uncalibrated
    pd_source: Mapped[str] = mapped_column(String(60), default="none")
    risk_category: Mapped[str] = mapped_column(String(16), index=True)
    policy_outcome: Mapped[str] = mapped_column(String(24))  # eligible | refer | not_eligible
    policy_rules: Mapped[list] = mapped_column(JSON, default=list)
    limit_low: Mapped[float] = mapped_column(Float, default=0.0)
    limit_high: Mapped[float] = mapped_column(Float, default=0.0)
    positive_factors: Mapped[list] = mapped_column(JSON, default=list)
    risk_factors: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    explanation_type: Mapped[str] = mapped_column(String(40), default="rule_based")
    model_version: Mapped[str] = mapped_column(String(40), default="")
    human_review_status: Mapped[str] = mapped_column(String(24), default="not_required")
    # not_required | pending | approved_to_proceed | declined_by_reviewer | info_requested
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# --------------------------------------------------------------------------- Forecast / Risk
class CashflowForecast(Base):
    __tablename__ = "cashflow_forecasts"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[dict] = mapped_column(JSON, default=dict)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_ending_balance: Mapped[float] = mapped_column(Float)
    min_balance: Mapped[float] = mapped_column(Float)
    low_balance_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    shortfall_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    shortfall_amount: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class RiskSignal(Base):
    __tablename__ = "risk_signals"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    priority_score: Mapped[float] = mapped_column(Float, index=True)
    priority_level: Mapped[str] = mapped_column(String(12), index=True)  # critical | high | medium | low
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_category: Mapped[str] = mapped_column(String(16), index=True)
    exposure: Mapped[float] = mapped_column(Float, default=0.0)
    primary_signal: Mapped[str] = mapped_column(String(40), index=True)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    days_to_stress: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    recommended_action: Mapped[str] = mapped_column(String(200))
    talking_points: Mapped[list] = mapped_column(JSON, default=list)
    assigned_rm: Mapped[str] = mapped_column(String(60), index=True)
    review_status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # open | in_review | escalated | resolved | dismissed
    missing_data: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RMFeedback(Base):
    __tablename__ = "rm_feedback"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), index=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    rm_name: Mapped[str] = mapped_column(String(60))
    action_taken: Mapped[str] = mapped_column(String(120))
    outcome: Mapped[str] = mapped_column(String(40))  # resolved | improving | no_change | worsening | escalated
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProductRecommendation(Base):
    __tablename__ = "product_recommendations"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    product: Mapped[str] = mapped_column(String(40))
    propensity_score: Mapped[float] = mapped_column(Float, index=True)  # 0-100, NOT a calibrated probability
    all_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    est_value: Mapped[float] = mapped_column(Float, default=0.0)
    indicators: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    outreach_action: Mapped[str] = mapped_column(String(200), default="")
    outreach_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# --------------------------------------------------------------------------- Workflows
class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    template: Mapped[str] = mapped_column(String(40), index=True)
    scenario: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    # queued | running | awaiting_review | completed | failed | cancelled
    current_node: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    final_output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    retry_of: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(60), default="demo-user")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowNodeEvent(Base):
    __tablename__ = "workflow_node_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(48))
    event_type: Mapped[str] = mapped_column(String(20))
    # started | completed | failed | interrupted | resumed | routed | skipped | run_started | run_completed | run_failed | run_cancelled
    status: Mapped[str] = mapped_column(String(20))
    input_data: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    output_data: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    metrics: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_node_events_run_seq", "run_id", "seq", unique=True),)


class HumanReviewDecision(Base):
    __tablename__ = "human_review_decisions"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    sme_id: Mapped[str] = mapped_column(ForeignKey("smes.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(24), index=True)  # kyc | credit | alert | workflow
    subject_id: Mapped[str] = mapped_column(String(40))
    review_kind: Mapped[str] = mapped_column(String(24))  # kyc_exception | credit_referral | risk_escalation
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | decided
    trigger_reasons: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    allowed_roles: Mapped[list] = mapped_column(JSON, default=list)
    action: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewer_role: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reviewer_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    previous_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    audit_event_id: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- Governance
class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60), index=True)
    category: Mapped[str] = mapped_column(String(24), index=True)  # workflow | kyc | credit | forecast | alert | prospecting | system | review
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | attention | critical
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(40))
    sme_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    is_ai_activity: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ModelMetric(Base):
    __tablename__ = "model_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_key: Mapped[str] = mapped_column(String(40), index=True)
    metric_name: Mapped[str] = mapped_column(String(48))
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    details: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    dataset_type: Mapped[str] = mapped_column(String(40), default="synthetic")
    note: Mapped[str] = mapped_column(Text, default="")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
