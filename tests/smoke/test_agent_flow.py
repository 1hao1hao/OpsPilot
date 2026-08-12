"""HTTP smoke for the current persistent Run API and legacy adapter."""

from __future__ import annotations

import time
import uuid


def make_alert(alert_id: str) -> dict:
    return {
        "alert_id": alert_id,
        "service_name": "order-service",
        "alert_type": "timeout",
        "severity": "P1",
        "timestamp": "2026-08-12T00:00:00Z",
        "signals": {"db": {"replication_lag_seconds": 18}},
    }


def wait_for_terminal(agent_client, run_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = agent_client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"SUCCEEDED", "FAILED"}:
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach terminal state")


def test_submit_status_and_result(agent_client):
    request_id = f"smoke-flow-{uuid.uuid4().hex}"
    response = agent_client.post(
        "/api/v1/runs",
        json={"request_id": request_id, "alert": make_alert(request_id)},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert wait_for_terminal(agent_client, run_id)["status"] == "SUCCEEDED"
    result = agent_client.get(f"/api/v1/runs/{run_id}/result")
    assert result.status_code == 200
    assert result.json()["report"]["primary_root_cause"]["root_cause_type"] == "db_replication_lag"


def test_missing_fields_returns_422(agent_client):
    response = agent_client.post("/api/v1/runs", json={"request_id": "invalid", "alert": {"alert_id": "bad"}})
    assert response.status_code == 422


def test_legacy_adapter_uses_persistent_run(agent_client):
    alert_id = f"legacy-smoke-{uuid.uuid4().hex}"
    response = agent_client.post("/api/v1/analyze", json=make_alert(alert_id))
    assert response.status_code == 202
    run_id = response.json()["trace_id"]
    assert wait_for_terminal(agent_client, run_id)["status"] == "SUCCEEDED"
    assert agent_client.get(f"/api/v1/analyze/{run_id}/result").status_code == 200
