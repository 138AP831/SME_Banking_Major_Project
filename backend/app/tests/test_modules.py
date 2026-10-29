import re

from app.tests.conftest import CA, KYC, RM, RISK


def test_kyc_samples_and_upload_masks_aadhaar(client):
    apps = client.get("/api/kyc/applications", headers=KYC).json()
    a8 = next(a for a in apps if a["sme_id"] == "SME-1008")
    samples = client.get("/api/kyc/samples/SME-1008", headers=KYC).json()["samples"]
    key = next(s["key"] for s in samples if s["doc_type"] == "AADHAAR" and s["variant"] == "clean")
    r = client.post(f"/api/kyc/applications/{a8['id']}/load-sample", json={"key": key}, headers=KYC)
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/kyc/applications/{a8['id']}", headers=KYC).json()
    doc = next(d for d in detail["documents"] if d["doc_type"] == "AADHAAR")
    blob = str(doc)
    assert not re.search(r"\b\d{4}\s?\d{4}\s?\d{4}\b", blob)  # a full 12-digit Aadhaar is never exposed
    assert "AADHAAR" not in detail["missing_documents"]


def test_kyc_rejects_bad_files(client):
    apps = client.get("/api/kyc/applications", headers=KYC).json()
    a = next(x for x in apps if x["sme_id"] == "SME-1008")
    r = client.post(f"/api/kyc/applications/{a['id']}/documents", files={"file": ("x.exe", b"MZ....", "application/octet-stream")}, headers=KYC)
    assert r.status_code == 400
    r = client.post(f"/api/kyc/applications/{a['id']}/documents", files={"file": ("empty.pdf", b"", "application/pdf")}, headers=KYC)
    assert r.status_code == 400


def test_kyc_scenario_exceptions_are_detected(client):
    apps = {a["sme_id"]: a for a in client.get("/api/kyc/applications", headers=KYC).json()}
    d = client.get(f"/api/kyc/applications/{apps['SME-1010']['id']}", headers=KYC).json()
    codes = {e["code"] for e in d["exceptions"]}
    assert {"ENTITY_NAME_MATCH", "SIGNATORY_MATCH"} <= codes
    assert apps["SME-1007"]["status"] == "eligible_for_approval"
    assert d["external_verification"]["performed"] is False


def test_credit_assessment_and_fallback(client):
    r = client.post("/api/credit/SME-1005/assess", json={"requested_amount": 4000000}, headers=CA).json()
    assert r["credit"]["pd_estimate"] is not None and r["credit"]["explanation_type"] == "shap"
    assert r["policy"]["outcome"] == "not_eligible"
    client.put("/api/credit/model-availability", json={"available": False}, headers=RISK)
    try:
        fb = client.post("/api/credit/SME-1005/assess", json={"requested_amount": 4000000}, headers=CA).json()
        assert fb["credit"]["pd_estimate"] is None and fb["credit"]["explanation_type"] != "shap"
    finally:
        client.put("/api/credit/model-availability", json={"available": True}, headers=RISK)


def test_cashflow_scenario_levers(client):
    r = client.post("/api/cashflow/SME-1001/scenario", json={"scenario": {"receipt_delay_days": 30, "revenue_decline_pct": 25}}, headers=RM).json()
    assert r["scenario"]["summary"]["min_balance"] < r["baseline"]["summary"]["min_balance"]
    fin = client.post("/api/cashflow/SME-1006/scenario", json={"scenario": {"additional_financing": 5_000_000, "financing_day": 3}}, headers=RM).json()
    assert fin["scenario"]["summary"]["min_balance"] > fin["baseline"]["summary"]["min_balance"]


def test_thin_file_prospect_is_flagged(client):
    r = client.get("/api/cashflow/SME-1040", headers=RM).json()
    assert r.get("available") is False or r["forecast"]["uncertainty"]


def test_prospecting_respects_consent(client):
    leads = client.get("/api/prospecting/leads", headers=RM).json()["leads"]
    blocked = [l for l in leads if l["consent_status"] != "granted"]
    assert blocked and all(not l["outreach_allowed"] for l in blocked)
    r = client.post("/api/prospecting/SME-1035/outreach", json={"action": "call"}, headers=RM)
    assert r.status_code == 403


def test_model_metrics_honest(client):
    m = client.get("/api/models/performance", headers=RM).json()
    assert m["ews"]["available"] is False and m["ews"]["reason"]
    assert "synthetic" in m["credit"]["dataset"].lower()


def test_policy_change_is_audited_and_role_gated(client):
    assert client.put("/api/governance/policy", json={"policy": {"pd_refer": 0.2}}, headers=RM).status_code == 403
    r = client.put("/api/governance/policy", json={"policy": {"pd_refer": 0.12}}, headers=RISK)
    assert r.status_code == 200 and r.json()["changed"]["pd_refer"]["to"] == 0.12
    client.put("/api/governance/policy", json={"policy": {"pd_refer": 0.10}}, headers=RISK)


def test_reset_reseeds(client):
    r = client.post("/api/admin/reset", json={"with_workflows": False}, headers=RM)
    assert r.status_code == 200 and r.json()["smes"] == 40
    assert client.get("/api/workflows/runs", headers=RM).json() == []
    assert client.get("/api/overview", headers=RM).json()["kpis"][0]["value"] == 23
