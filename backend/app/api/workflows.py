from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Ctx, get_ctx, get_db, staff
from app.graphs.builder import SCENARIOS, TEMPLATES, graph_spec
from app.models.entities import SME, HumanReviewDecision, WorkflowRun
from app.services import review as review_svc
from app.services import workflow_runner as wr
from app.services.audit import log_audit

router = APIRouter(prefix="/api", tags=["workflows"], dependencies=[Depends(staff)])


@router.get("/workflows/templates")
def templates():
    return [{"id": k, "label": v["label"], "description": v["description"], "nodes": [n for st in v["steps"] for n in (st if isinstance(st, list) else [st])]} for k, v in TEMPLATES.items()]


@router.get("/workflows/scenarios")
def scenarios(db: Session = Depends(get_db)):
    out = []
    for k, v in SCENARIOS.items():
        s = db.get(SME, v["sme_id"])
        out.append({"id": k, **v, "sme_name": s.name if s else None})
    return out


@router.get("/workflows/graph/{template}")
def graph(template: str):
    if template not in TEMPLATES:
        raise HTTPException(404, "Unknown template")
    return graph_spec(template)


@router.post("/workflows/runs")
def start(body: dict, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    template, sme_id, scenario = body.get("template"), body.get("sme_id"), body.get("scenario")
    if scenario:
        sc = SCENARIOS.get(scenario)
        if not sc:
            raise HTTPException(400, "Unknown scenario")
        template, sme_id = template or sc["template"], sme_id or sc["sme_id"]
    if not template or not sme_id:
        raise HTTPException(400, "template and sme_id (or scenario) are required")
    fault = body.get("inject_fault")
    try:
        run_id = wr.start_run(sme_id, template, body.get("params"), ctx.user, ctx.role, scenario=scenario, inject_fault=fault)
    except LookupError:
        raise HTTPException(404, f"SME {sme_id} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"run_id": run_id, "status": "queued"}


@router.get("/workflows/runs")
def runs(sme_id: str | None = None, status: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    q = select(WorkflowRun, SME.name).join(SME, SME.id == WorkflowRun.sme_id).order_by(WorkflowRun.started_at.desc()).limit(limit)
    if sme_id:
        q = q.where(WorkflowRun.sme_id == sme_id)
    if status:
        q = q.where(WorkflowRun.status == status)
    return [wr.run_dict(r, n) for r, n in db.execute(q)]


@router.get("/workflows/runs/{run_id}")
def run(run_id: str, db: Session = Depends(get_db)):
    r = db.get(WorkflowRun, run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    reviews = [review_svc.view(db, h) for h in db.scalars(select(HumanReviewDecision).where(HumanReviewDecision.run_id == run_id))]
    return {**wr.run_dict(r, db.get(SME, r.sme_id).name), "events": wr.events_after(run_id, 0), "reviews": reviews}


@router.get("/workflows/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0):
    return wr.events_after(run_id, after)


@router.get("/workflows/runs/{run_id}/stream")
def stream(run_id: str, request: Request, after: int = 0, db: Session = Depends(get_db)):
    if not db.get(WorkflowRun, run_id):
        raise HTTPException(404, "Run not found")
    last_id = request.headers.get("last-event-id")
    if last_id and last_id.isdigit():
        after = max(after, int(last_id))
    return StreamingResponse(wr.sse_stream(run_id, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/workflows/runs/{run_id}/cancel")
def cancel(run_id: str, ctx: Ctx = Depends(get_ctx)):
    if not wr.cancel_run(run_id, ctx.user, ctx.role):
        raise HTTPException(409, "The run is already finished.")
    return {"ok": True, "note": "Cancellation takes effect at the next node boundary."}


@router.post("/workflows/runs/{run_id}/retry")
def retry(run_id: str, ctx: Ctx = Depends(get_ctx)):
    try:
        return {"run_id": wr.retry_run(run_id, ctx.user, ctx.role)}
    except LookupError:
        raise HTTPException(404, "Run not found")


# ----------------------------------------------------------------------------- human review queue
@router.get("/reviews")
def reviews(status: str | None = "pending", ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    return review_svc.queue(db, ctx.role, None if status == "all" else status)


@router.get("/reviews/{review_id}")
def review_detail(review_id: str, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    row = db.get(HumanReviewDecision, review_id)
    if not row:
        raise HTTPException(404, "Review not found")
    return review_svc.view(db, row, ctx.role)


@router.post("/reviews/{review_id}/decision")
def decide(review_id: str, body: dict, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    try:
        res = review_svc.decide(db, review_id, body.get("action", ""), body.get("reason", ""), ctx.role, ctx.user)
    except review_svc.ReviewError as e:
        db.rollback()
        raise HTTPException(e.status, str(e))
    db.commit()
    resumed = False
    if res["run_id"]:
        try:
            wr.resume_run(res["run_id"], res["resume_payload"])
            resumed = True
        except ValueError:
            resumed = False
    return {**{k: v for k, v in res.items() if k != "resume_payload"}, "workflow_resumed": resumed}
