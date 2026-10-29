from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import core, modules, portal, workflows
from app.config import get_settings
from app.db import seed
from app.services import workflow_runner

log = logging.getLogger("smeflow")
CUSTOMER_OK = ("/api/portal", "/api/health", "/api/roles", "/docs", "/openapi.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed.ensure_seeded()
    n = workflow_runner.recover_orphans()
    if n:
        log.warning("Marked %d interrupted run(s) as failed", n)
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="SMEFlow Intelligence API", version="0.1.0", lifespan=lifespan,
                  description="AI-powered SME banking prototype. All data is synthetic; external checks are simulated.")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

    @app.middleware("http")
    async def role_guard(request: Request, call_next):
        # SME Customer role is confined to the portal (defence in depth; routers also enforce staff-only access)
        if request.headers.get("x-demo-role") == "SME Customer" and request.url.path.startswith("/api") and not request.url.path.startswith(CUSTOMER_OK):
            return JSONResponse({"detail": "The SME Customer role can only use the SME portal."}, status_code=403)
        return await call_next(request)

    for r in (core.router, workflows.router, modules.router, portal.router):
        app.include_router(r)
    return app


app = create_app()
