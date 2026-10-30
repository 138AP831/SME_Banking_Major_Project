"""External Market Data for the Customer Portal (business growth intelligence).

Uses Google Places only if GOOGLE_PLACES_API_KEY is configured and the call succeeds. Otherwise deterministic, clearly
labelled SYNTHETIC demo data is returned - the app never pretends to have retrieved live information.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.config import get_settings

log = logging.getLogger("smeflow.market")
_PREFIX = ["Apex", "Bright", "Crest", "Delta", "Evergreen", "Falcon", "Granite", "Harbor", "Indus", "Jade", "Kiran", "Lotus"]
_SUFFIX = {"Manufacturing": "Fabricators", "Retail": "Traders", "Logistics": "Carriers", "Food": "Foods", "Services": "Solutions", "IT": "Digital"}
SYNTHETIC_NOTE = "SYNTHETIC DEMO DATA - no live market lookup was made. Names, counts and ratings below are generated for illustration only."


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def _synthetic(city: str, industry: str) -> dict[str, Any]:
    s = _seed(city, industry)
    suffix = next((v for k, v in _SUFFIX.items() if k.lower() in industry.lower()), "Enterprises")
    comps = []
    for i in range(5):
        comps.append({"name": f"{_PREFIX[(s + i * 3) % len(_PREFIX)]} {suffix} (synthetic)", "distance_km": round(0.8 + ((s >> i) % 90) / 10, 1), "rating": round(3.4 + ((s >> (i + 3)) % 15) / 10, 1),
                      "is_synthetic": True})
    density = ["low", "moderate", "high"][s % 3]
    return {"source": "synthetic_demo", "is_synthetic": True, "note": SYNTHETIC_NOTE, "location": city, "category": industry, "competitors": comps, "signals": {
        "nearby_competitors": 12 + s % 30, "competitor_density": density, "average_rating": round(3.9 + (s % 8) / 10, 1), "demand_trend": ["stable", "growing", "growing", "softening"][s % 4],
        "footfall_index": 60 + s % 35}}


def _live(city: str, industry: str, key: str) -> dict[str, Any]:
    import httpx

    r = httpx.post("https://places.googleapis.com/v1/places:searchText", timeout=6.0,
                   headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,places.formattedAddress"},
                   json={"textQuery": f"{industry} businesses in {city}", "maxResultCount": 10})
    r.raise_for_status()
    places = r.json().get("places", [])
    comps = [{"name": (p.get("displayName") or {}).get("text", "Unnamed"), "rating": p.get("rating"), "reviews": p.get("userRatingCount"), "address": p.get("formattedAddress"), "is_synthetic": False} for p in places[:8]]
    rated = [c["rating"] for c in comps if c["rating"]]
    n = len(places)
    return {"source": "google_places", "is_synthetic": False, "note": "Live listings from Google Places (top results only).", "location": city, "category": industry, "competitors": comps,
            "signals": {"nearby_competitors": n, "competitor_density": "high" if n >= 8 else "moderate" if n >= 4 else "low", "average_rating": round(sum(rated) / len(rated), 1) if rated else None,
                        "demand_trend": None, "footfall_index": None}}


def fetch(city: str, industry: str) -> dict[str, Any]:
    key = get_settings().google_places_api_key
    if key:
        try:
            return _live(city, industry, key)
        except Exception as e:  # noqa: BLE001
            log.warning("Google Places lookup failed (%s); using labelled synthetic data", type(e).__name__)
            d = _synthetic(city, industry)
            d["note"] = f"Google Places lookup failed ({type(e).__name__}). " + d["note"]
            return d
    return _synthetic(city, industry)


def growth_intelligence(market: dict[str, Any], readiness: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """SWOT and growth recommendations from market signals + the customer's own readiness indicators (never bank-internal data)."""
    sig, ind = market.get("signals", {}), {i["code"]: i for i in (readiness or {}).get("indicators", [])}
    S, W, O, T = [], [], [], []
    for i in ind.values():
        (S if i["status"] == "good" else W if i["status"] == "gap" else []).append(i["label"] + ": " + i["explanation"])
    dens, trend = sig.get("competitor_density"), sig.get("demand_trend")
    if trend == "growing":
        O.append("Local demand for your category looks to be growing.")
    if dens == "low":
        O.append("Few nearby competitors - room to gain share.")
    if sig.get("average_rating") and sig["average_rating"] < 4.0:
        O.append("Competitors average modest ratings - service quality can differentiate you.")
    if dens == "high":
        T.append("Many competitors nearby - price pressure and customer churn are likely.")
    if trend == "softening":
        T.append("Demand signals are softening - plan for slower receipts.")
    if not O:
        O.append("Look for adjacent customer segments in the same area.")
    if not T:
        T.append("Competitors may respond if you grow quickly.")
    recs = []
    if "revenue_diversification" in ind and ind["revenue_diversification"]["status"] != "good":
        recs.append("Target two or three new customers in nearby segments so no single customer exceeds about a third of your income.")
    if dens == "high":
        recs.append("Differentiate on reliability and turnaround time, and ask satisfied customers for reviews.")
    if trend == "growing":
        recs.append("Plan capacity ahead of the demand growth; use your improvement plan to strengthen your cash buffer first.")
    recs.append("Revisit this view after your next reassessment to see how the market and your readiness have moved.")
    return {"swot": {"strengths": S[:4] or ["Build more documented strengths by completing your improvement plan."], "weaknesses": W[:4] or ["No major weaknesses flagged on the illustrative criteria."], "opportunities": O[:4], "threats": T[:4]},
            "recommendations": recs[:5], "is_synthetic": bool(market.get("is_synthetic")), "source": market.get("source")}
