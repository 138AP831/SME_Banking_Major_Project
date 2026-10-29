"""Scenario outcomes must hold on any 'today', not only on the date they were tuned."""
from datetime import date

import pytest

from app.db import synthetic as syn
from app.ml import forecast as fc
from app.services import analysis

ROSTER = {r["id"]: r for r in syn.ROSTER}


def _forecast(sme_id, as_of):
    r = ROSTER[sme_id]
    g = syn.generate_transactions(r, as_of, 20261029)
    sme = {"sanctioned_limit": r.get("sanctioned", 0), "utilization": r.get("util_end", 0)}
    return fc.forecast_cashflow(g.txns, as_of, 60, None, facility_headroom=max(0, sme["sanctioned_limit"] * (1 - sme["utilization"])))


@pytest.mark.parametrize("as_of", [date(2026, 10, 29), date(2027, 1, 15), date(2027, 4, 3), date(2027, 8, 20)])
def test_scenario_a_never_short(as_of):
    assert _forecast("SME-1001", as_of)["summary"]["days_to_shortfall"] is None


@pytest.mark.parametrize("as_of", [date(2026, 10, 29), date(2027, 1, 15), date(2027, 4, 3), date(2027, 8, 20)])
def test_scenario_b_always_short(as_of):
    d = _forecast("SME-1006", as_of)["summary"]["days_to_shortfall"]
    assert d is not None and d <= 45
