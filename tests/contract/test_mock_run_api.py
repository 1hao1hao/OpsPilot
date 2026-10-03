"""Mock scenario E2E adapter uses the current persistent Run API."""

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeprca.mock_env import create_mock_router
from deeprca.mock_env.alert_simulator import SCENARIOS
from opspilot.models import CreateRunRequest


def test_mock_scenario_submits_polls_and_reads_compact_report(monkeypatch):
    name, scenario = next(iter(SCENARIOS.items()))
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            payload = CreateRunRequest.model_validate(json.loads(request.content))
            assert payload.request_id.startswith("scenario-")
            return httpx.Response(202, json={"run_id": "mock-run"})
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "report": {
                        "primary_root_cause": {
                            "summary": scenario["expected_root_cause"],
                            "confidence": 0.99,
                        }
                    }
                },
            )
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    app = FastAPI()
    app.include_router(create_mock_router())
    with TestClient(app) as client:
        response = client.post(f"/api/v1/mock/scenarios/{name}/run")
    assert response.status_code == 200
    assert response.json()["actual_confidence"] == 0.99
    assert requests == [
        ("POST", "/api/v1/runs"),
        ("GET", "/api/v1/runs/mock-run"),
        ("GET", "/api/v1/runs/mock-run/result"),
    ]
