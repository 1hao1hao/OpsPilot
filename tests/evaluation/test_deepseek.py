import json
from datetime import UTC, datetime

import httpx
import pytest

from opspilot.evaluation.systems import DeepSeekHybridSystem, DeepSeekLLMOnlySystem, DeepSeekToolsSystem
from opspilot.llm import DeepSeekRCAClient
from opspilot.models import AlertEvent


def _response(candidate="db_replication_lag"):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "candidate_types": [candidate],
                            "evidence_types": ["db.replication_lag"],
                            "rationale": "Database replica lag is elevated.",
                        }
                    )
                },
            }
        ],
        "model": "deepseek-v4-flash",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _alert():
    return AlertEvent(
        alert_id="a-1",
        service_name="order-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 1, tzinfo=UTC),
        description="read requests are timing out",
        signals={"db": {"replication_lag_seconds": 18},
                 "trace": {"traces": [{"trace_id": "db", "spans": [{"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 10}, {"span_id": "db", "parent_span_id": "root", "service": "mysql", "status": "ERROR", "duration_ms": 1500}]}]}},
    )


@pytest.mark.asyncio
async def test_client_uses_json_mode_and_records_api_token_usage_without_leaking_key():
    captured = {}

    def handler(request: httpx.Request):
        captured.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=_response())

    client = DeepSeekRCAClient(
        api_key="test-secret",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    result = await client.diagnose(alert={"description": "timeout"})

    assert captured["response_format"] == {"type": "json_object"}
    assert captured["thinking"] == {"type": "disabled"}
    assert result.decision.candidate_types[0].value == "db_replication_lag"
    assert result.usage.total_tokens == 120
    assert "test-secret" not in repr(client.__dict__)


@pytest.mark.asyncio
async def test_three_ablation_systems_receive_only_their_allowed_inputs():
    calls = []

    class StubClient:
        async def diagnose(self, **kwargs):
            calls.append(kwargs)
            candidate = (kwargs.get("allowed_candidates") or ["db_replication_lag"])[0]
            response = _response(candidate)
            transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
            client = DeepSeekRCAClient(api_key="stub", model="deepseek-v4-flash", transport=transport)
            return await client.diagnose(**kwargs)

    systems = [DeepSeekLLMOnlySystem(StubClient()), DeepSeekToolsSystem(StubClient()), DeepSeekHybridSystem(StubClient())]
    predictions = [await system.predict(_alert(), f"case-{index}") for index, system in enumerate(systems)]

    assert "signals" not in calls[0]["alert"]
    assert calls[0].get("tool_observations") is None
    assert calls[1]["tool_observations"]["db.replication"] == {"replication_lag_seconds": 18}
    assert calls[1].get("evidence") is None
    assert "db.replication_lag" in {item["evidence_type"] for item in calls[2]["evidence"]}
    assert calls[2]["allowed_candidates"][0] == "db_replication_lag"
    assert all(prediction["token_usage"]["total_tokens"] == 120 for prediction in predictions)
