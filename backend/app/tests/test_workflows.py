import json

from app.tests.conftest import CA, CUST, KYC, RISK, RM, start, wait_for

ALL_NODES = {"load_sme_profile", "validate_data", "build_shared_features", "run_propensity_analysis", "run_credit_assessment", "forecast_cash_flow", "analyze_risk_signals",
             "evaluate_policy_rules", "human_review_gate", "generate_explanation", "persist_results"}


def completed_nodes(run):
    return {e["node"] for e in run["events"] if e["event_type"] == "completed"}


def test_templates_and_graph(client):
    t = client.get("/api/workflows/templates", headers=RM).json()
    assert len(t) == 5
    g = client.get("/api/workflows/graph/full_lifecycle", headers=RM).json()
    assert {n["id"] for n in g["nodes"]} == ALL_NODES
    assert any(e["kind"] == "parallel" for e in g["edges"])


def test_scenario_a_straight_through(client):
    run = wait_for(client, start(client, scenario="A"))
    assert run["status"] == "completed", run["error"]
    assert run["final_output"]["policy_outcome"] == "eligible"
    assert run["reviews"] == []
    assert run["final_output"]["headline"]["days_to_shortfall"] is None
    done = completed_nodes(run)
    assert done == ALL_NODES - {"human_review_gate"}
    seqs = [e["seq"] for e in run["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    skipped_gate = [e for e in run["events"] if e["node"] == "human_review_gate"]
    assert skipped_gate == []  # conditional edge: gate never entered


def test_scenario_b_liquidity_stress_hitl(client):
    run = wait_for(client, start(client, scenario="B"))
    assert run["status"] == "awaiting_review", run
    assert run["final_output"] is None
    rv = run["reviews"][0]
    assert rv["status"] == "pending" and rv["kind"] in ("credit_referral", "risk_escalation")
    assert "FCST-001" in {r["rule_id"] for r in rv["reasons"]}  # the projected shortfall is among the triggers
    assert run["events"][-1]["event_type"] == "run_paused"
    assert "persist_results" not in completed_nodes(run)  # nothing is persisted / approved before the human decides
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "approve", "reason": "looks fine to me, go ahead"}, headers=KYC)
    assert r.status_code == 403
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "approve", "reason": "ok"}, headers=RISK)
    assert r.status_code == 400
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "approve", "reason": "Shortfall confirmed; proceed with a liquidity line review."}, headers=RISK)
    assert r.status_code == 200 and r.json()["workflow_resumed"]
    done = wait_for(client, run["id"], ("completed", "failed"))
    assert done["status"] == "completed", done["error"]
    assert done["final_output"]["review"]["action"] == "approve"
    assert done["final_output"]["headline"]["days_to_shortfall"] is not None
    assert any(e["event_type"] == "resumed" for e in done["events"])
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "reject", "reason": "changing my mind later on"}, headers=RISK)
    assert r.status_code == 409
    audit = client.get("/api/governance/audit", params={"sme_id": "SME-1006", "category": "review"}, headers=RM).json()
    assert any(e["action"] == "review_approve" for e in audit["events"])
    alerts = client.get("/api/ews/alerts", headers=RM).json()["alerts"]
    assert any(a["sme_id"] == "SME-1006" and a["priority_level"] in ("critical", "high") for a in alerts)


def test_scenario_c_kyc_exception(client):
    run = wait_for(client, start(client, scenario="C"))
    assert run["status"] == "awaiting_review"
    rv = run["reviews"][0]
    assert rv["kind"] == "kyc_exception"
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "approve", "reason": "Everything is fine, approving this now."}, headers=CA)
    assert r.status_code == 403
    # approval is impossible while documents are missing
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "approve", "reason": "Override: verified in person at the branch visit."}, headers=KYC)
    assert r.status_code == 400 and "missing" in r.text.lower()
    r = client.post(f"/api/reviews/{rv['id']}/decision", json={"action": "request_info", "reason": "Request a corrected GST certificate and utility bill."}, headers=KYC)
    assert r.status_code == 200
    done = wait_for(client, run["id"], ("completed", "failed"))
    assert done["status"] == "completed"
    app = client.get("/api/kyc/applications", headers=KYC).json()
    assert next(a for a in app if a["sme_id"] == "SME-1004")["status"] == "info_requested"


def test_sse_stream(client):
    run_id = start(client, template="prospecting", sme_id="SME-1013")
    wait_for(client, run_id)
    with client.stream("GET", f"/api/workflows/runs/{run_id}/stream", headers=RM) as r:
        body = "".join(r.iter_text())
    events = [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ") and "node" in l]
    assert events[0]["event_type"] == "run_started" and events[-1]["event_type"] == "run_completed"
    # resume from Last-Event-ID
    with client.stream("GET", f"/api/workflows/runs/{run_id}/stream", headers={**RM, "Last-Event-ID": str(events[-2]["seq"])}) as r:
        body2 = "".join(r.iter_text())
    assert body2.count("event: node_event") == 1


def test_fault_injection_and_retry(client):
    run_id = start(client, template="credit_assessment", sme_id="SME-1013", inject_fault="run_credit_assessment")
    run = wait_for(client, run_id)
    assert run["status"] == "failed"
    failed = [e for e in run["events"] if e["event_type"] == "failed"]
    assert failed and failed[0]["node"] == "run_credit_assessment" and "Fault injected" in failed[0]["error"]["message"]
    retry = client.post(f"/api/workflows/runs/{run_id}/retry", headers=RM).json()["run_id"]
    r2 = wait_for(client, retry)
    assert r2["status"] == "completed" and r2["retry_of"] == run_id


def test_cancel_run(client):
    from app.config import get_settings

    s = get_settings()
    s.demo_step_delay_ms = 150
    try:
        run_id = start(client, scenario="A")
        client.post(f"/api/workflows/runs/{run_id}/cancel", headers=RM)
        run = wait_for(client, run_id, ("cancelled", "completed", "failed"))
    finally:
        s.demo_step_delay_ms = 0
    assert run["status"] == "cancelled"


def test_customer_role_confined(client):
    assert client.get("/api/overview", headers=CUST).status_code == 403
    assert client.get("/api/workflows/runs", headers=CUST).status_code == 403
    assert client.get("/api/portal/me", headers=CUST).status_code == 200


def test_pii_masking(client):
    base = client.get("/api/smes/SME-1005/360", headers=RM).json()
    assert "•" in base["profile"]["pan"]
    denied = client.get("/api/smes/SME-1005/360", params={"reveal": True}, headers=RM).json()
    assert "•" in denied["profile"]["pan"] and denied["reveal_denied"]
    ok = client.get("/api/smes/SME-1005/360", params={"reveal": True}, headers=CA).json()
    assert "•" not in ok["profile"]["pan"]
