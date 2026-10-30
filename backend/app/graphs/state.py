from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class WFState(TypedDict, total=False):
    run_id: str
    sme_id: str
    template: str
    params: dict[str, Any]
    as_of: str
    sme: dict[str, Any]
    kyc: dict[str, Any]
    data_quality: dict[str, Any]
    features: dict[str, Any]
    propensity: dict[str, Any]
    credit: dict[str, Any]
    forecast: dict[str, Any]
    risk: dict[str, Any]
    policy: dict[str, Any]
    review: dict[str, Any]
    explanation: dict[str, Any]
    persisted: dict[str, Any]
    # customer-side workflows (customer-safe state only: no bank-restricted fields are ever placed here)
    customer: dict[str, Any]
    readiness: dict[str, Any]
    improvement: dict[str, Any]
    market: dict[str, Any]
    growth: dict[str, Any]
    progress: dict[str, Any]
    customer_submission: dict[str, Any]  # bank workflows: consent-gated view of the customer's submission
    warnings: Annotated[list, operator.add]
