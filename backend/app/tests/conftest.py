import os
import tempfile
import time
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="smeflow-test-"))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp / 'test.db'}", "CHECKPOINT_DB_PATH": str(_tmp / "cp.sqlite"), "MODELS_DIR": str(_tmp / "models"), "UPLOAD_DIR": str(_tmp / "uploads"),
    "SAMPLE_DOCS_DIR": str(_tmp / "samples"), "DEMO_STEP_DELAY_MS": "0", "BOOTSTRAP_WORKFLOWS": "false", "LLM_PROVIDER": "none",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

RM = {"X-Demo-Role": "Relationship Manager"}
CA = {"X-Demo-Role": "Credit Analyst"}
KYC = {"X-Demo-Role": "KYC Analyst"}
RISK = {"X-Demo-Role": "Risk Manager"}
CUST = {"X-Demo-Role": "SME Customer"}


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def wait_for(client, run_id, statuses=("completed", "failed", "cancelled", "awaiting_review"), timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/workflows/runs/{run_id}", headers=RM).json()
        if r["status"] in statuses:
            return r
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach {statuses}; last={r['status']}")


def start(client, **body):
    r = client.post("/api/workflows/runs", json=body, headers=RM)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]
