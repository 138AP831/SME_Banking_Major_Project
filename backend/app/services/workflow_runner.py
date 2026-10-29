"""Run lifecycle: start / execute / pause at HITL gate / resume / cancel / retry, plus SSE helpers."""
from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from sqlalchemy import select

from app.config import get_settings
from app.core.clock import get_as_of_date
from app.core.util import to_jsonable
from app.db.session import session_scope
from app.graphs import execution_events as ee
from app.graphs.builder import TEMPLATES, build_graph, flat_nodes
from app.models.entities import SME, HumanReviewDecision, WorkflowNodeEvent, WorkflowRun, new_id, utcnow
from app.services.audit import log_audit

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="wf")
_conn: sqlite3.Connection | None = None
_conn_lock = threading.Lock()
TERMINAL = ("completed", "failed", "cancelled")


def _checkpointer() -> SqliteSaver:
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = sqlite3.connect(str(get_settings().checkpoint_db_path), check_same_thread=False)
            _conn.execute("PRAGMA journal_mode=WAL")
        return SqliteSaver(_conn)


def reset_checkpoints() -> None:
    global _conn
    with _conn_lock:
        if _conn is not None:
            _conn.close()
            _conn = None
    p = get_settings().checkpoint_db_path
    for suffix in ("", "-wal", "-shm"):
        try:
            __import__("os").remove(str(p) + suffix)
        except FileNotFoundError:
            pass


def _set_status(run_id: str, status: str, **kw) -> None:
    with session_scope() as s:
        r = s.get(WorkflowRun, run_id)
        r.status = status
        for k, v in kw.items():
            setattr(r, k, v)
        if status in TERMINAL:
            r.completed_at = utcnow()
            r.duration_ms = int((r.completed_at - r.started_at).total_seconds() * 1000)


def start_run(sme_id: str, template: str, params: dict | None, triggered_by: str, role: str, scenario: str | None = None, retry_of: str | None = None,
              inject_fault: str | None = None, wait: bool = False) -> str:
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template '{template}'")
    params = dict(params or {})
    with session_scope() as s:
        sme = s.get(SME, sme_id)
        if not sme:
            raise LookupError(sme_id)
        run_id = new_id("RUN")
        s.add(WorkflowRun(id=run_id, sme_id=sme_id, template=template, scenario=scenario, status="queued", params=to_jsonable(params), triggered_by=triggered_by, retry_of=retry_of))
        s.flush()
        log_audit(s, actor=triggered_by, role=role, action="workflow_started", category="workflow", entity_type="workflow_run", entity_id=run_id, sme_id=sme_id, run_id=run_id,
                  summary=f"Workflow '{TEMPLATES[template]['label']}' started for {sme.name}" + (f" (retry of {retry_of})" if retry_of else ""),
                  details={"template": template, "scenario": scenario, "params": params}, is_ai=True)
    if inject_fault:
        if inject_fault not in flat_nodes(template):
            raise ValueError(f"'{inject_fault}' is not a node of template '{template}'")
        ee.FAULTS[run_id] = inject_fault
    fut = _pool.submit(_execute, run_id, sme_id, template, params, str(get_as_of_date()))
    if wait:
        fut.result()
    return run_id


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _drive(run_id: str, template: str, inp: Any) -> None:
    graph = build_graph(template, _checkpointer())
    cfg = _config(run_id)
    interrupted = False
    for chunk in graph.stream(inp, cfg, stream_mode="updates"):
        if "__interrupt__" in chunk:
            interrupted = True
    if interrupted or graph.get_state(cfg).next:
        _await_review(run_id)
    else:
        _finish(run_id, graph.get_state(cfg).values)


def _await_review(run_id: str) -> None:
    _set_status(run_id, "awaiting_review")
    ee.emit(run_id, "human_review_gate", "run_paused", "awaiting_review", metrics={"note": "Run is paused; resumes only after an authorised reviewer decides."})


def _finish(run_id: str, values: dict) -> None:
    out = {"policy_outcome": (values.get("policy") or {}).get("outcome"), "explanation": (values.get("explanation") or {}).get("text"), "persisted": values.get("persisted"),
           "review": values.get("review"), "warnings": values.get("warnings"),
           "headline": {"credit_score": (values.get("credit") or {}).get("model_score"), "risk_category": (values.get("credit") or {}).get("risk_category"),
                        "days_to_shortfall": ((values.get("forecast") or {}).get("summary") or {}).get("days_to_shortfall"), "priority": (values.get("risk") or {}).get("priority_score"),
                        "top_product": (values.get("propensity") or {}).get("product")}}
    _set_status(run_id, "completed", final_output=to_jsonable(out), current_node=None)
    ee.emit(run_id, "__end__", "run_completed", "completed", output_data=out)


def _execute(run_id: str, sme_id: str, template: str, params: dict, as_of: str) -> None:
    try:
        _set_status(run_id, "running")
        ee.emit(run_id, "__start__", "run_started", "running", input_data={"sme_id": sme_id, "template": template, "as_of": as_of, "params": params})
        _drive(run_id, template, {"run_id": run_id, "sme_id": sme_id, "template": template, "params": params, "as_of": as_of, "warnings": []})
    except ee.RunCancelled:
        _cancelled(run_id)
    except GraphInterrupt:
        _await_review(run_id)
    except Exception as e:  # noqa: BLE001
        _failed(run_id, e)


def _cancelled(run_id: str) -> None:
    _set_status(run_id, "cancelled")
    ee.emit(run_id, "__end__", "run_cancelled", "cancelled")
    with session_scope() as s:
        for r in s.scalars(select(HumanReviewDecision).where(HumanReviewDecision.run_id == run_id, HumanReviewDecision.status == "pending")):
            r.status = "decided"
            r.action = "cancelled"
            r.reason = "Workflow run cancelled"
    ee.CANCELLED.discard(run_id)


def _failed(run_id: str, e: Exception) -> None:
    err = {"type": type(e).__name__, "message": str(e)}
    _set_status(run_id, "failed", error=err)
    ee.emit(run_id, "__end__", "run_failed", "failed", error=err)
    with session_scope() as s:
        r = s.get(WorkflowRun, run_id)
        log_audit(s, actor="SMEFlow AI", role="system", action="workflow_failed", category="workflow", entity_type="workflow_run", entity_id=run_id, sme_id=r.sme_id, run_id=run_id,
                  summary=f"Workflow '{r.template}' failed: {err['message'][:160]}", severity="critical", details=err, is_ai=True)


def resume_run(run_id: str, payload: dict) -> None:
    with session_scope() as s:
        r = s.get(WorkflowRun, run_id)
        if not r or r.status != "awaiting_review":
            raise ValueError("Run is not awaiting review.")
        template = r.template
    _set_status(run_id, "running")

    def go():
        try:
            _drive(run_id, template, Command(resume=payload))
        except ee.RunCancelled:
            _cancelled(run_id)
        except GraphInterrupt:
            _await_review(run_id)
        except Exception as e:  # noqa: BLE001
            _failed(run_id, e)

    _pool.submit(go)


def cancel_run(run_id: str, actor: str, role: str) -> bool:
    with session_scope() as s:
        r = s.get(WorkflowRun, run_id)
        if not r or r.status in TERMINAL:
            return False
        status = r.status
        sme_id = r.sme_id
        log_audit(s, actor=actor, role=role, action="workflow_cancel_requested", category="workflow", entity_type="workflow_run", entity_id=run_id, sme_id=sme_id, run_id=run_id,
                  summary=f"Cancellation requested for run {run_id}")
    if status == "awaiting_review":
        _cancelled(run_id)
    else:
        ee.CANCELLED.add(run_id)  # cooperative: takes effect at the next node boundary
    return True


def retry_run(run_id: str, actor: str, role: str) -> str:
    with session_scope() as s:
        r = s.get(WorkflowRun, run_id)
        if not r:
            raise LookupError(run_id)
        args = (r.sme_id, r.template, dict(r.params or {}), actor, role, r.scenario, run_id)
    return start_run(*args)


def recover_orphans() -> int:
    """Runs left 'running' by a previous server process cannot continue; mark them failed. awaiting_review runs are resumable (checkpointed)."""
    n = 0
    with session_scope() as s:
        for r in s.scalars(select(WorkflowRun).where(WorkflowRun.status.in_(["queued", "running"]))):
            r.status = "failed"
            r.error = {"type": "ServerRestart", "message": "The server restarted while this run was in progress. Use Retry to start a new run."}
            r.completed_at = utcnow()
            n += 1
    return n


def run_blocking(sme_id: str, template: str, params: dict | None = None, **kw) -> str:
    return start_run(sme_id, template, params, kw.pop("triggered_by", "system"), kw.pop("role", "system"), wait=True, **kw)


# ----------------------------------------------------------------------------- views / SSE
def event_dict(e: WorkflowNodeEvent) -> dict:
    return {"seq": e.seq, "run_id": e.run_id, "node": e.node, "event_type": e.event_type, "status": e.status, "input": e.input_data, "output": e.output_data, "metrics": e.metrics,
            "error": e.error, "duration_ms": e.duration_ms, "timestamp": e.timestamp.isoformat(timespec="milliseconds") + "Z"}


def run_dict(r: WorkflowRun, sme_name: str | None = None) -> dict:
    return {"id": r.id, "sme_id": r.sme_id, "sme_name": sme_name, "template": r.template, "template_label": TEMPLATES[r.template]["label"], "scenario": r.scenario, "status": r.status,
            "current_node": r.current_node, "params": r.params, "final_output": r.final_output, "error": r.error, "retry_of": r.retry_of, "triggered_by": r.triggered_by,
            "started_at": r.started_at.isoformat(timespec="seconds") + "Z", "completed_at": r.completed_at.isoformat(timespec="seconds") + "Z" if r.completed_at else None,
            "duration_ms": r.duration_ms}


def events_after(run_id: str, after: int = 0) -> list[dict]:
    with session_scope() as s:
        return [event_dict(e) for e in s.scalars(select(WorkflowNodeEvent).where(WorkflowNodeEvent.run_id == run_id, WorkflowNodeEvent.seq > after).order_by(WorkflowNodeEvent.seq))]


def sse_stream(run_id: str, after: int = 0, poll_s: float = 0.15, max_idle_s: float = 3600) -> Iterator[str]:
    """Yields SSE frames from persisted events. Ends once the run is terminal or paused and all events were sent."""
    import json

    last = after
    idle_start = time.time()
    yield "retry: 1500\n\n"
    while True:
        evs = events_after(run_id, last)
        for e in evs:
            last = e["seq"]
            yield f"id: {e['seq']}\nevent: node_event\ndata: {json.dumps(e, default=str)}\n\n"
        if evs:
            idle_start = time.time()
        with session_scope() as s:
            st = s.get(WorkflowRun, run_id).status
        if not evs and st in (*TERMINAL, "awaiting_review"):
            yield f"event: run_state\ndata: {json.dumps({'status': st})}\n\n"
            return
        if time.time() - idle_start > max_idle_s:
            return
        if not evs:
            yield ": keepalive\n\n"
        time.sleep(poll_s)
