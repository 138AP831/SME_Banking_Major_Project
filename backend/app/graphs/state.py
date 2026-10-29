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
    warnings: Annotated[list, operator.add]
