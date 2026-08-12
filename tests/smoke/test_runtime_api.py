"""Compose smoke for the Stage-3 persistent API/worker chain."""

from __future__ import annotations

import time
import uuid


def test_persistent_run_completes_through_independent_worker(agent_client):
    request_id = f"compose-smoke-{uuid.uuid4().hex}"
    payload = {
        "request_id": request_id,
        "alert": {
            "alert_id": request_id,
            "service_name": "checkout-service",
            "alert_type": "timeout",
            "severity": "P1",
            "timestamp": "2026-08-12T00:00:00Z",
            "signals": {"db": {"replication_lag_seconds": 20}},
        },
    }
    accepted = agent_client.post("/api/v1/runs", json=payload)
    assert accepted.status_code == 202
    run_id = accepted.json()["run_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = agent_client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.2)
    assert run["status"] == "SUCCEEDED"
    result = agent_client.get(f"/api/v1/runs/{run_id}/result")
    assert result.status_code == 200
    assert result.json()["report"]["primary_root_cause"]["root_cause_type"] == "db_replication_lag"
