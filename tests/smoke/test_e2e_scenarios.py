"""Representative business snapshots through the persistent HTTP chain."""

from __future__ import annotations

import time
import uuid

import pytest

SCENARIOS = [
    ({"db": {"replication_lag_seconds": 20}}, "db_replication_lag"),
    ({"redis": {"memory_usage_percent": 96, "hit_rate_percent": 93}}, "redis_memory_pressure"),
    ({"kafka": {"consumer_lag": 12000}}, "kafka_consumer_lag"),
    ({"rpc": {"timeout_rate": 0.45}}, "rpc_timeout"),
]


@pytest.mark.parametrize(("signals", "expected"), SCENARIOS)
def test_business_scenario(agent_client, signals, expected):
    request_id = f"scenario-{uuid.uuid4().hex}"
    accepted = agent_client.post(
        "/api/v1/runs",
        json={
            "request_id": request_id,
            "alert": {
                "alert_id": request_id,
                "service_name": "checkout-service",
                "alert_type": "timeout",
                "severity": "P1",
                "timestamp": "2026-08-12T00:00:00Z",
                "signals": signals,
            },
        },
    )
    run_id = accepted.json()["run_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = agent_client.get(f"/api/v1/runs/{run_id}").json()["status"]
        if status in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.2)
    assert status == "SUCCEEDED"
    report = agent_client.get(f"/api/v1/runs/{run_id}/result").json()["report"]
    assert report["primary_root_cause"]["root_cause_type"] == expected
