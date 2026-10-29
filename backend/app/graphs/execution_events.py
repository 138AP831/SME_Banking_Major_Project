"""Real execution events: every LangGraph node is wrapped so start/finish/fail/interrupt are persisted
(WorkflowNodeEvent) as they actually happen. The SSE endpoint streams these rows."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable

from sqlalchemy import func, select

from app.config import get_settings
from app.core.util import summarize, to_jsonable
from app.db.session import session_scope
from app.models.entities import WorkflowNodeEvent, WorkflowRun, utcnow

_emit_lock = threading.Lock()
CANCELLED: set[str] = set()
FAULTS: dict[str, str] = {}  # run_id -> node to fail (labelled testing option)


class RunCancelled(Exception):
    pass


class InjectedFault(Exception):
    pass


def emit(run_id: str, node: str, event_type: str, status: str, input_data: Any = None, output_data: Any = None, metrics: Any = None, error: Any = None,
         duration_ms: int | None = None) -> int:
    with _emit_lock, session_scope() as s:
        run = s.get(WorkflowRun, run_id)
        run.event_seq = (run.event_seq or 0) + 1
        seq = run.event_seq
        s.add(WorkflowNodeEvent(run_id=run_id, seq=seq, node=node, event_type=event_type, status=status, input_data=summarize(to_jsonable(input_data)) if input_data is not None else None,
                                output_data=summarize(to_jsonable(output_data)) if output_data is not None else None, metrics=to_jsonable(metrics), error=to_jsonable(error),
                                duration_ms=duration_ms, timestamp=utcnow()))
        if event_type == "started" or event_type == "resumed":
            run.current_node = node
    return seq


def _was_interrupted(run_id: str, node: str) -> bool:
    with session_scope() as s:
        last = s.scalars(select(WorkflowNodeEvent).where(WorkflowNodeEvent.run_id == run_id, WorkflowNodeEvent.node == node, WorkflowNodeEvent.event_type != "run_paused").order_by(WorkflowNodeEvent.seq.desc())).first()
        return bool(last and last.event_type == "interrupted")


def traced(name: str, fn: Callable[[dict], dict], input_keys: tuple[str, ...] = (), is_gate: bool = False) -> Callable[[dict], dict]:
    """Wrap a node function with real event emission."""
    from langgraph.errors import GraphInterrupt

    def wrapper(state: dict) -> dict:
        run_id = state["run_id"]
        if run_id in CANCELLED:
            raise RunCancelled()
        resumed = is_gate and _was_interrupted(run_id, name)
        inp = {k: state.get(k) for k in input_keys if k in state}
        emit(run_id, name, "resumed" if resumed else "started", "running", input_data=inp)
        t0 = time.perf_counter()
        try:
            if FAULTS.get(run_id) == name:
                FAULTS.pop(run_id, None)
                raise InjectedFault(f"Fault injected into '{name}' (testing option).")
            out = fn(state)
        except GraphInterrupt:
            emit(run_id, name, "interrupted", "awaiting_review", duration_ms=int((time.perf_counter() - t0) * 1000), metrics={"reason": "human_review_required"})
            raise
        except RunCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            emit(run_id, name, "failed", "failed", error={"type": type(e).__name__, "message": str(e), "trace": traceback.format_exc(limit=4)},
                 duration_ms=int((time.perf_counter() - t0) * 1000))
            raise
        dur = int((time.perf_counter() - t0) * 1000)
        delay = get_settings().demo_step_delay_ms
        if delay:
            time.sleep(delay / 1000)
        if run_id in CANCELLED:
            raise RunCancelled()
        metrics = out.pop("_metrics", None) if isinstance(out, dict) else None
        emit(run_id, name, "completed", "completed", output_data={k: v for k, v in out.items() if k != "trace"}, metrics=metrics, duration_ms=dur)
        return out

    wrapper.__name__ = name
    return wrapper
