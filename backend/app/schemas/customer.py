"""Customer-safe response schemas. Fields are an explicit allow-list: anything not declared here (internal scores, exposure, alerts,
RM notes, escalations, policy rules) cannot reach the Customer Portal even if a service accidentally returns it."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IndicatorOut(_Out):
    code: str
    label: str
    status: str
    value: str
    target: str
    explanation: str
    weight: int


class ChangeOut(_Out):
    previous_index: int
    delta: int
    previous_status_label: str


class ReadinessOut(_Out):
    id: str
    version: int
    status: str
    status_label: str
    readiness_index: int
    indicators: list[IndicatorOut]
    strengths: list[dict[str, str]]
    gaps: list[dict[str, str]]
    explanation: str
    is_reassessment: bool
    created_at: str
    criteria_version: str
    disclaimer: str
    submission_version: int | None = None
    change: ChangeOut | None = None


class ReadinessEnvelope(_Out):
    latest: ReadinessOut | None = None
    history: list[dict[str, Any]]
    consent_granted: bool
    can_assess: bool
    disclaimer: str


class PlanItemOut(_Out):
    id: str
    code: str
    title: str
    why: str
    how: str
    status: str
    completed_at: str | None = None
    latest_indicator_status: str | None = None  # what the latest assessment says about this area: good | watch | gap


class ProgressOut(_Out):
    total: int
    completed: int
    open: int
    percent: int


class PlanOut(_Out):
    items: list[PlanItemOut]
    progress: ProgressOut
    based_on_assessment_version: int | None = None


class ConsentPurposeOut(_Out):
    purpose: str
    label: str
    description: str
    direction: str
    shares: list[str]
    not_shared: str
    granted: bool
    updated_at: str | None = None


class ConsentOut(_Out):
    purposes: list[ConsentPurposeOut]
    history: list[dict[str, Any]]
    note: str


class ProfileOut(_Out):
    revenue: dict[str, Any]
    expenses: dict[str, Any]
    liabilities: dict[str, Any]
    repayment: dict[str, Any]
    cash_flow: dict[str, Any]
    health: dict[str, Any]
    submission_version: int | None = None
    note: str


class GrowthOut(_Out):
    consent_required: bool
    market: dict[str, Any] | None = None
    growth: dict[str, Any] | None = None
    created_at: str | None = None
