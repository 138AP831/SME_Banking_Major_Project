"""Tests for the two-sided architecture update: privacy boundary, consent gate, monitoring->escalation, customer workflows, audit."""
import time

import pytest

from app.services.exchange import CUSTOMER_SME_FIELDS, find_forbidden
from app.tests.conftest import CA, CUST, KYC, RISK, RM, wait_for

BANK_ONLY_GETS = ["/api/ews/alerts", "/api/reviews", "/api/governance/audit", "/api/bank/escalations", "/api/bank/architecture", "/api/bank/intake/SME-1003", "/api/bank/exchange/SME-1003",
                  "/api/bank/decision-support/SME-1003", "/api/smes", "/api/smes/SME-1003/360", "/api/kyc/applications", "/api/workflows/runs", "/api/credit/SME-1003", "/api/models/performance"]


def poll(fn, timeout=40):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = fn()
        if last:
            return last
        time.sleep(0.2)
    raise AssertionError(f"condition not met within {timeout}s; last={last}")


def cust(client, method, path, sme="SME-1006", **kw):
    sep = "&" if "?" in path else "?"
    return getattr(client, method)(f"{path}{sep}sme_id={sme}", headers=CUST, **kw)


# ------------------------------------------------------------------ Scenario 4: data privacy
def test_customer_role_is_denied_every_bank_api(client):
    for p in BANK_ONLY_GETS:
        r = client.get(p, headers=CUST)
        assert r.status_code == 403, f"{p} returned {r.status_code}"
    for p, body in [("/api/bank/cases/ALT-X/contacts", {}), ("/api/bank/cases/ALT-X/escalate", {}), ("/api/reviews/REV-X/decision", {}), ("/api/workflows/runs", {"scenario": "A"})]:
        assert client.post(p, json=body, headers=CUST).status_code == 403


def test_customer_payloads_contain_no_bank_restricted_fields(client):
    for p in ["/api/portal/me", "/api/portal/consent", "/api/portal/submission", "/api/portal/financial-profile", "/api/portal/readiness", "/api/portal/plan", "/api/portal/growth", "/api/portal/activity"]:
        r = cust(client, "get", p, sme="SME-1005")
        assert r.status_code == 200, (p, r.text)
        assert find_forbidden(r.json()) == [], p
        blob = r.text.lower()
        assert "bureau" not in blob and "pd_estimate" not in blob and "policy_rules" not in blob, p


def test_allowlist_drops_bank_fields_from_the_sme_record():
    full = {k: 1 for k in CUSTOMER_SME_FIELDS} | {"bureau_score": 700, "outstanding_exposure": 5, "utilization": 0.4, "sanctioned_limit": 9, "assigned_rm": "x", "pan": "ABCDE1234F", "gstin": "g"}
    from app.services.exchange import customer_sme_view

    assert set(customer_sme_view(full)) == set(CUSTOMER_SME_FIELDS)
    assert find_forbidden({"a": [{"bureau_score": 1}]}) == ["a[0].bureau_score"]


def test_only_the_customer_can_change_consent_or_submit(client):
    assert client.post("/api/portal/consent?sme_id=SME-1006", json={"purpose": "loan_application", "granted": True}, headers=RM).status_code == 403
    assert client.post("/api/portal/submission?sme_id=SME-1006", json={}, headers=RM).status_code == 403
    assert client.post("/api/portal/readiness/run?sme_id=SME-1006", json={}, headers=RM).status_code == 403


# ------------------------------------------------------------------ Consent & exchange gate (+ scenario 1 intake)
def test_consent_gate_controls_what_the_bank_can_read(client):
    sme = "SME-1013"  # has a seeded credit application => seeded (synthetic) loan_application consent
    assert client.get(f"/api/bank/exchange/{sme}", headers=RM).json()["consent"]["loan_application"]["granted"] is True
    # customer submits business information (step 1) - held in the customer data domain
    body = {"business_profile": {"description": "Auto components"}, "financials": {"annual_revenue": 42_000_000, "annual_expenses": 36_000_000}, "existing_loans": [{"lender": "Demo Bank", "outstanding": 800000, "monthly_emi": 25000}],
            "requested_amount": 3_000_000, "tenure_months": 24, "purpose": "Inventory financing", "location": {"city": "Pune"}}
    assert cust(client, "post", "/api/portal/submission", sme=sme, json=body).status_code == 200
    ok = client.get(f"/api/bank/intake/{sme}", headers=RM).json()
    assert ok["customer_submission"]["authorised"] and ok["customer_submission"]["submission"]["financials"]["annual_revenue"] == 42_000_000
    assert set(ok) >= {"business_profile", "financial_statements", "bank_data", "existing_liabilities", "repayment_history", "kyc", "customer_submission"}
    # customer withdraws consent: the bank immediately loses access to the submission
    assert cust(client, "post", "/api/portal/consent", sme=sme, json={"purpose": "loan_application", "granted": False}).status_code == 200
    blocked = client.get(f"/api/bank/intake/{sme}", headers=RM).json()["customer_submission"]
    assert blocked["authorised"] is False and blocked["submission"] is None
    # and a new request cannot be sent to the bank
    assert cust(client, "post", "/api/portal/credit-request", sme=sme, json={"amount": 500000, "purpose": "x"}).status_code == 403
    # restore
    cust(client, "post", "/api/portal/consent", sme=sme, json={"purpose": "loan_application", "granted": True})
    assert cust(client, "post", "/api/portal/credit-request", sme=sme, json={"amount": 500000, "purpose": "Working capital"}).status_code == 200
    actions = {e["action"] for e in client.get(f"/api/governance/audit?sme_id={sme}&category=consent&limit=200", headers=RM).json()["events"]}
    assert {"consent_withdrawn", "consent_granted", "exchange_access_granted", "exchange_access_denied"} <= actions


# ------------------------------------------------------------------ Scenario 1: bank decision support
def test_decision_support_is_explainable_and_illustrative(client):
    r = client.post("/api/credit/SME-1005/assess", json={"requested_amount": 4000000}, headers=CA)
    assert r.status_code == 200
    d = client.get("/api/bank/decision-support/SME-1005", headers=RM).json()
    assert d["outcome"] in ("approve_for_consideration", "refer_for_human_review", "decline_illustrative")
    assert "illustrative" in d["outcome_label"].lower() and d["loan_approved"] is False and d["is_illustrative"] is True
    assert d["why"]["explanation"] and "policy_rules" in d["why"]


def test_bank_workflow_reads_submission_only_through_the_gate(client):
    sme = "SME-1005"
    run = wait_for(client, client.post("/api/workflows/runs", json={"template": "credit_assessment", "sme_id": sme}, headers=RM).json()["run_id"])
    ev = next(e for e in run["events"] if e["node"] == "load_sme_profile" and e["event_type"] == "completed")
    gate = ev["output"]["customer_submission"]
    assert set(gate) >= {"authorised", "purpose", "shared_fields"}


# ------------------------------------------------------------------ Scenario 3: customer readiness journey (+ privacy of the customer graphs)
def test_customer_readiness_journey_and_reassessment(client):
    sme = "SME-1006"
    # consent is required before any analysis
    assert cust(client, "post", "/api/portal/readiness/run", sme=sme, json={}).status_code == 403
    for p in ("readiness_assessment", "market_intelligence"):
        assert cust(client, "post", "/api/portal/consent", sme=sme, json={"purpose": p, "granted": True}).status_code == 200
    sub = {"financials": {"annual_revenue": 30_000_000, "annual_expenses": 27_000_000}, "existing_loans": [{"lender": "Demo Bank", "outstanding": 1_500_000, "monthly_emi": 90000}], "requested_amount": 3_000_000,
           "purpose": "Working capital", "location": {"city": "Mumbai"}}
    assert cust(client, "post", "/api/portal/submission", sme=sme, json=sub).status_code == 200
    assert cust(client, "post", "/api/portal/submission", sme=sme, json={"financials": {}}).status_code == 422  # revenue is required
    prof = cust(client, "get", "/api/portal/financial-profile", sme=sme).json()
    assert prof["revenue"]["avg_monthly_inflow"] > 0 and prof["submission_version"] == 1

    rid = cust(client, "post", "/api/portal/readiness/run", sme=sme, json={}).json()["run_id"]
    poll(lambda: cust(client, "get", f"/api/portal/runs/{rid}", sme=sme).json()["status"] == "completed")
    r1 = cust(client, "get", "/api/portal/readiness", sme=sme).json()
    latest = r1["latest"]
    assert latest["version"] == 1 and latest["status"] in ("likely_ready", "nearly_ready", "needs_improvement", "more_information_needed")
    assert "not a loan approval" in latest["disclaimer"].lower() and latest["indicators"] and all(i["explanation"] for i in latest["indicators"])

    # the customer-side graph never carried bank-restricted data
    detail = client.get(f"/api/workflows/runs/{rid}", headers=RM).json()
    assert detail["template"] == "customer_readiness"
    for e in detail["events"]:
        assert find_forbidden(e.get("input")) == [] and find_forbidden(e.get("output")) == [], e["node"]
    prof_ev = next(e for e in detail["events"] if e["node"] == "load_customer_profile" and e["event_type"] == "completed")
    assert set(prof_ev["output"]["sme"]) <= set(CUSTOMER_SME_FIELDS) and prof_ev["metrics"]["bank_fields_loaded"] == 0
    assert "evaluate_policy_rules" not in {e["node"] for e in detail["events"]}  # no bank policy engine on the customer side

    # improvement plan (workflow 4 runs after the readiness run) and market view
    plan = poll(lambda: (lambda p: p if p["items"] else None)(cust(client, "get", "/api/portal/plan", sme=sme).json()))
    item = plan["items"][0]
    assert item["why"] and item["how"]
    done = cust(client, "post", f"/api/portal/plan/{item['id']}", sme=sme, json={"done": True}).json()
    assert done["progress"]["completed"] >= 1
    g = poll(lambda: (lambda x: x if x.get("market") else None)(cust(client, "get", "/api/portal/growth", sme=sme).json()))
    assert g["market"]["is_synthetic"] is True and "SYNTHETIC" in g["market"]["note"] and g["growth"]["swot"]["strengths"]

    # update information, then request a fresh assessment: it must use the latest submission
    sub["financials"]["annual_revenue"] = 33_000_000
    assert cust(client, "post", "/api/portal/submission", sme=sme, json=sub).json()["version"] == 2
    rid2 = cust(client, "post", "/api/portal/readiness/run", sme=sme, json={}).json()["run_id"]
    poll(lambda: cust(client, "get", f"/api/portal/runs/{rid2}", sme=sme).json()["status"] == "completed")
    r2 = cust(client, "get", "/api/portal/readiness", sme=sme).json()
    assert r2["latest"]["version"] == 2 and r2["latest"]["is_reassessment"] is True and r2["latest"]["submission_version"] == 2
    assert len(r2["history"]) == 2 and r2["latest"]["change"] is not None
    kept = cust(client, "get", "/api/portal/plan", sme=sme).json()
    assert next(i for i in kept["items"] if i["id"] == item["id"])["status"] in ("done", "resolved")  # progress persists across reassessment
    # customer-visible activity trail
    acts = {e["action"] for e in cust(client, "get", "/api/portal/activity", sme=sme).json()["events"]}
    assert {"consent_granted", "customer_submission_saved", "readiness_assessed", "reassessment_completed"} <= acts


# ------------------------------------------------------------------ Scenario 2: existing loan monitoring -> intervention
def test_monitoring_contact_verify_and_escalation(client):
    alerts = [a for a in client.get("/api/ews/alerts", headers=RM).json()["alerts"] if a["sme_id"] not in ("SME-1001", "SME-1004", "SME-1006")]
    assert alerts, "seed data should contain open alerts"
    a = alerts[-1]  # lowest priority: least impact on other tests
    aid = a["id"]
    assert a["priority_score"] is not None  # step 5: ranked
    # cannot escalate before contact & verification
    assert client.post(f"/api/bank/cases/{aid}/escalate", json={"target": "Collections", "reason": "no contact has happened"}, headers=RM).status_code == 409
    # only case roles can record contact
    assert client.post(f"/api/bank/cases/{aid}/contacts", json={}, headers=KYC).status_code == 403
    bad = client.post(f"/api/bank/cases/{aid}/contacts", json={"channel": "phone", "issue_verified": "confirmed", "outcome": "no_change", "business_situation": "short"}, headers=RM)
    assert bad.status_code == 400
    ok = client.post(f"/api/bank/cases/{aid}/contacts", headers=RM, json={"channel": "phone", "issue_verified": "confirmed", "outcome": "no_change", "business_situation": "Owner confirms a delayed receivable from the largest customer.",
                                                                           "commitment_amount": 150000, "commitment_due": "2030-01-31", "notes": "Will clear the overdue instalment."})
    assert ok.status_code == 200, ok.text
    case = ok.json()
    assert case["alert"]["review_status"] == "in_review" and case["contacts"][0]["commitment"]["status"] == "pending"
    assert case["eligibility"]["can_escalate"] is True and case["eligibility"]["trigger"] == "distress_persists"
    cid = case["contacts"][0]["id"]
    missed = client.post(f"/api/bank/contacts/{cid}/commitment", json={"status": "missed"}, headers=RM).json()
    assert missed["eligibility"]["trigger"] == "both"
    # escalate: reason, target, evidence snapshot, status
    assert client.post(f"/api/bank/cases/{aid}/escalate", json={"target": "Treasury", "reason": "x" * 20}, headers=RM).status_code == 400
    esc = client.post(f"/api/bank/cases/{aid}/escalate", json={"target": "Collections", "reason": "Commitment missed and cash position still deteriorating."}, headers=RM)
    assert esc.status_code == 200, esc.text
    e = esc.json()["escalations"][0]
    assert e["target"] == "Collections" and e["status"] == "open" and e["evidence"]["contacts"] and esc.json()["alert"]["review_status"] == "escalated"
    assert client.post(f"/api/bank/cases/{aid}/escalate", json={"target": "Collections", "reason": "second attempt should be refused"}, headers=RM).status_code == 409
    # receiving team (human) accepts then closes with a recorded resolution
    assert client.post(f"/api/bank/escalations/{e['id']}/status", json={"status": "accepted"}, headers=RM).status_code == 403
    assert client.post(f"/api/bank/escalations/{e['id']}/status", json={"status": "accepted"}, headers=RISK).status_code == 200
    assert client.post(f"/api/bank/escalations/{e['id']}/status", json={"status": "closed", "resolution": "x"}, headers=RISK).status_code == 400
    assert client.post(f"/api/bank/escalations/{e['id']}/status", json={"status": "closed", "resolution": "Restructured instalments agreed with the borrower."}, headers=RISK).status_code == 200
    # everything is on the audit trail and in the case timeline
    final = client.get(f"/api/bank/cases/{aid}", headers=RM).json()
    actions = {t["action"] for t in final["timeline"]}
    assert {"rm_contact_recorded", "commitment_missed", "case_escalated"} <= actions
    # and the customer cannot see any of it
    assert client.get(f"/api/bank/cases/{aid}", headers=CUST).status_code == 403
    assert client.get("/api/bank/escalations", headers=CUST).status_code == 403


# ------------------------------------------------------------------ Scenario 5: auditability
def test_audit_trail_covers_both_portals(client):
    cats = set(client.get("/api/governance/audit?limit=1", headers=RM).json()["categories"])
    assert {"consent", "customer", "monitoring"} <= cats
    ctrl = {c["control"] for c in client.get("/api/governance/summary", headers=RM).json()["controls"]}
    assert {"Consent & exchange gate", "Customer-safe boundary"} <= ctrl
    arch = client.get("/api/bank/architecture", headers=RM).json()
    assert [d["id"] for d in arch["data_layer"]] == ["bank_restricted", "exchange_gate", "customer_domain", "external_market"]
