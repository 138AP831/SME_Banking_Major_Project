"""Graph factory: one StateGraph per workflow template, built from the shared node library."""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graphs.execution_events import traced
from app.graphs.nodes import NODE_META, NODES
from app.graphs.state import WFState

# (nodes in a parallel group are fanned out then joined before the next step)
TEMPLATES: dict[str, dict[str, Any]] = {
    "full_lifecycle": {
        "label": "Full SME lifecycle", "description": "Discover, assess, forecast and monitor one SME end to end: propensity, credit, cash flow and early-warning in a single run.",
        "steps": ["load_sme_profile", "validate_data", "build_shared_features", ["run_propensity_analysis", "run_credit_assessment", "forecast_cash_flow"], "analyze_risk_signals",
                  "evaluate_policy_rules", "human_review_gate", "generate_explanation", "persist_results"]},
    "prospecting": {
        "label": "Prospecting & next-best product", "description": "Rank an SME for its most suitable product, respecting marketing consent.",
        "steps": ["load_sme_profile", "validate_data", "build_shared_features", "run_propensity_analysis", "evaluate_policy_rules", "generate_explanation", "persist_results"]},
    "kyc_onboarding": {
        "label": "KYC onboarding review", "description": "Re-checks the onboarding case, applies the KYC policy and routes exceptions to a KYC analyst.",
        "steps": ["load_sme_profile", "validate_data", "build_shared_features", "evaluate_policy_rules", "human_review_gate", "generate_explanation", "persist_results"]},
    "credit_assessment": {
        "label": "Credit assessment", "description": "Preliminary credit assessment with KYC gating, policy rules and a referral gate for a credit analyst.",
        "steps": ["load_sme_profile", "validate_data", "build_shared_features", "run_credit_assessment", "evaluate_policy_rules", "human_review_gate", "generate_explanation", "persist_results"]},
    "cashflow_ews": {
        "label": "Cash-flow & early warning", "description": "Forecast liquidity and detect early-warning signals; escalates large critical alerts to a human.",
        "steps": ["load_sme_profile", "validate_data", "build_shared_features", ["run_credit_assessment", "forecast_cash_flow"], "analyze_risk_signals", "evaluate_policy_rules",
                  "human_review_gate", "generate_explanation", "persist_results"]},
}
TEMPLATES.update({
    "customer_readiness": {
        "label": "Customer: financial readiness", "description": "Customer Portal workflow: customer-safe profile, financial analysis, cash-flow outlook and an indicative loan-readiness view. Uses no bank-restricted data.",
        "steps": ["load_customer_profile", "validate_data", "build_shared_features", "forecast_cash_flow", "assess_loan_readiness", "explain_readiness", "persist_customer_results"]},
    "customer_improvement": {
        "label": "Customer: improvement & reassessment", "description": "Customer Portal workflow: improvement plan from the latest readiness assessment, external market data, SWOT and growth recommendations.",
        "steps": ["load_customer_profile", "generate_improvement_plan", "fetch_market_intelligence", "build_growth_intelligence", "persist_customer_results"]},
})
# Which portal / architecture workflow each template belongs to (for the Studio and the architecture endpoint)
PORTAL_OF = {"full_lifecycle": ("bank", "Full lifecycle"), "prospecting": ("bank", "Prospecting"), "kyc_onboarding": ("bank", "KYC onboarding"),
             "credit_assessment": ("bank", "New loan applications (steps 1-3)"), "cashflow_ews": ("bank", "Existing loan monitoring (steps 4-5)"),
             "customer_readiness": ("customer", "Customer financial readiness (steps 1-3)"), "customer_improvement": ("customer", "Customer improvement & reassessment (steps 4-6)")}
CUSTOMER_TEMPLATE_IDS = ("customer_readiness", "customer_improvement")
SCENARIOS = {
    "A": {"label": "A - Healthy borrower", "sme_id": "SME-1001", "template": "full_lifecycle", "expect": "Straight-through: eligible, no review, no shortfall."},
    "B": {"label": "B - Liquidity stress", "sme_id": "SME-1006", "template": "full_lifecycle", "expect": "Projected shortfall, critical alert, escalation review (HITL)."},
    "C": {"label": "C - KYC exception", "sme_id": "SME-1004", "template": "kyc_onboarding", "expect": "Document mismatches; pauses for a KYC analyst."},
}


def flat_nodes(template: str) -> list[str]:
    return [n for st in TEMPLATES[template]["steps"] for n in (st if isinstance(st, list) else [st])]


def graph_spec(template: str) -> dict:
    """Static structure for the Studio canvas (nodes + edges + which nodes the template skips)."""
    steps = TEMPLATES[template]["steps"]
    edges, prev = [], ["__start__"]
    for st in steps:
        group = st if isinstance(st, list) else [st]
        for p in prev:
            for g in group:
                edges.append({"source": p, "target": g, "kind": "parallel" if len(group) > 1 or len(prev) > 1 else "sequence"})
        prev = group
    for p in prev:
        edges.append({"source": p, "target": "__end__", "kind": "sequence"})
    active = set(flat_nodes(template))
    gate = [e for e in edges if e["target"] == "human_review_gate"]
    return {"template": template, **{k: TEMPLATES[template][k] for k in ("label", "description")},
            "nodes": [{"id": n, **NODE_META[n], "active": n in active} for n in NODES], "edges": edges,
            "conditional": ({"node": "human_review_gate", "note": "Only entered when the policy engine requires a human review; otherwise the graph skips to generate_explanation."} if gate else None)}


def build_graph(template: str, checkpointer):
    spec = TEMPLATES[template]["steps"]
    g = StateGraph(WFState)
    for n in flat_nodes(template):
        fn, keys = NODES[n]
        g.add_node(n, traced(n, fn, keys, is_gate=(n == "human_review_gate")))
    prev: list[str] = [START]
    for st in spec:
        group = st if isinstance(st, list) else [st]
        if "human_review_gate" in group:
            # conditional routing: the gate is entered only when policy requires review
            nxt = spec[spec.index(st) + 1]
            g.add_conditional_edges(prev[0], lambda s, nxt=nxt: "human_review_gate" if (s.get("policy") or {}).get("requires_review") else nxt, ["human_review_gate", nxt])
            prev = group
            continue
        if len(prev) == 1:
            for x in group:
                g.add_edge(prev[0], x)
        else:
            if len(group) == 1:
                g.add_edge(prev, group[0])  # join: wait for all parallel branches
            else:
                raise ValueError("parallel group directly after parallel group is not supported")
        prev = group
    g.add_edge(prev[0], END)
    return g.compile(checkpointer=checkpointer)
