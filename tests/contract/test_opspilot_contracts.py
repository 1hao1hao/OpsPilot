from __future__ import annotations

from datetime import UTC, datetime
from time import sleep

import pytest
from pydantic import ValidationError

from opspilot.compat import LegacyGraphAdapter
from opspilot.graph import OpsPilotWorkflow
from opspilot.models import AlertEvent, RootCauseType, ToolCall
from opspilot.tools import ToolExecutor, build_default_registry
from opspilot.tools.errors import ToolValidationError, UnknownToolError


def make_alert(**signals) -> AlertEvent:
    return AlertEvent(
        alert_id="contract-alert",
        service_name="order-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 1, tzinfo=UTC),
        description="generic latency increase",
        signals=signals,
    )


def test_alert_rejects_unknown_fields_and_naive_timestamp():
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({**make_alert().model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({**make_alert().model_dump(), "timestamp": "2026-08-01T10:00:00"})


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool_and_invalid_input():
    executor = ToolExecutor(build_default_registry())
    with pytest.raises(UnknownToolError):
        await executor.execute(ToolCall(tool_call_id="x", tool_name="unknown", arguments={}))
    with pytest.raises(ToolValidationError):
        await executor.execute(ToolCall(tool_call_id="x", tool_name="db.inspect", arguments={"bad": "input"}))


@pytest.mark.asyncio
async def test_db_and_normal_reports_are_valid_with_fake_summary():
    workflow = OpsPilotWorkflow(build_default_registry())
    db_report = await workflow.analyze(make_alert(db={"replication_lag_seconds": 20}))
    assert db_report.primary_root_cause.root_cause_type == RootCauseType.DB_REPLICATION_LAG
    assert "db.replication_lag" in {item.evidence_type for item in db_report.evidence}
    normal_report = await workflow.analyze(make_alert(metric={"cpu_usage": 0.5}))
    assert normal_report.primary_root_cause.root_cause_type == RootCauseType.NO_FAULT
    assert normal_report.status == "completed"


@pytest.mark.asyncio
async def test_legacy_adapter_and_new_workflow_share_root_cause_and_evidence_types():
    alert = make_alert(db={"active_connections": 98, "max_connections": 100})
    new_report = await OpsPilotWorkflow(build_default_registry()).analyze(alert, trace_id="same-trace")
    old_state = await LegacyGraphAdapter().ainvoke(
        {"alert": alert.model_dump(mode="json"), "trace_id": "same-trace", "start_time": "2026-08-01T00:00:00Z"}
    )
    assert old_state["root_cause"]["best_candidate"]["root_cause_type"] == new_report.primary_root_cause.root_cause_type.value
    assert {item["evidence_type"] for item in old_state["collected_evidence"]["top_evidences"]} == {
        item.evidence_type for item in new_report.evidence
    }


def test_agents_do_not_import_io_clients():
    from pathlib import Path

    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/opspilot/agents").glob("*.py"))
    assert "import httpx" not in source
    assert "import redis" not in source
    assert "import sqlalchemy" not in source.lower()


def test_legacy_http_endpoint_runs_the_opspilot_adapter(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from deeprca.api import routes
    from deeprca.main import create_app

    monkeypatch.setattr(routes.analysis_store, "_ensure_redis", AsyncMock(return_value=None))
    routes._compiled_graph = None
    payload = make_alert(db={"replication_lag_seconds": 20}).model_dump(mode="json")
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/analyze", json=payload)
        assert response.status_code == 202
        trace_id = response.json()["trace_id"]
        for _ in range(50):
            result = client.get(f"/api/v1/analyze/{trace_id}/result")
            if result.status_code == 200:
                break
            sleep(0.01)
    assert result.status_code == 200
    assert result.json()["root_cause"]["best_candidate"]["root_cause_type"] == "db_replication_lag"
