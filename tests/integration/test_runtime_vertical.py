from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from opspilot.api.app import create_app
from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunStatus
from opspilot.persistence import Database
from opspilot.persistence.repositories import InvalidStateTransition, RuntimeRepository
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.queue import InMemoryRunQueue
from opspilot.runtime.task_manager import TaskManager
from opspilot.runtime.worker import RuntimeWorker
from opspilot.tools import build_default_registry


def alert() -> AlertEvent:
    return AlertEvent(
        alert_id="runtime-db-alert",
        service_name="order-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        description="read requests are timing out",
        signals={"db": {"replication_lag_seconds": 18}, "metric": {"cpu_usage": 0.42}, "trace": {"traces": [{"trace_id": "db", "spans": [{"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 10}, {"span_id": "db", "parent_span_id": "root", "service": "mysql", "status": "ERROR", "duration_ms": 1500}]}]}},
    )


@pytest.fixture
async def stack(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    await database.create_schema()
    repository = RuntimeRepository(database.sessions)
    queue = InMemoryRunQueue()
    settings = RuntimeSettings(database_url="sqlite+aiosqlite://", recovery_stale_seconds=0)
    manager = TaskManager(repository, queue, settings)
    worker = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, build_default_registry(), settings),
        settings,
    )
    yield database, repository, queue, manager, worker
    await database.dispose()


@pytest.mark.asyncio
async def test_api_queue_worker_database_vertical_chain(stack):
    _database, repository, queue, manager, worker = stack
    app = create_app(task_manager=manager)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
            response = await client.post(
                "/api/v1/runs",
                json={"request_id": "request-vertical-1", "alert": alert().model_dump(mode="json")},
            )
            assert response.status_code == 202
            run_id = response.json()["run_id"]
            assert list(queue.messages) == [run_id]  # Redis-equivalent payload contains only run_id.

            queued = await client.get(f"/api/v1/runs/{run_id}")
            assert queued.json()["status"] == "QUEUED"
            assert await repository.get_report(run_id) is None

            assert await worker.process_once(timeout_seconds=0.01)
            result = await client.get(f"/api/v1/runs/{run_id}/result")
            assert result.status_code == 200
            assert result.json()["status"] == "SUCCEEDED"
            assert result.json()["report"]["primary_root_cause"]["root_cause_type"] == "db_replication_lag"
            investigation = result.json()["report"]["investigation"]
            assert "db" in investigation["invoked_experts"]
            assert "db.replication" in investigation["executed_tools"]
            events = (await client.get(f"/api/v1/runs/{run_id}/events")).json()
            assert events[0]["event_type"] == "run.created"
            assert events[-1]["event_type"] == "run.succeeded"
            planned = next(item for item in events if item["event_type"] == "investigation.action.planned")
            assert planned["detail"]["action"]["action_type"] == "invoke_expert"
            assert planned["detail"]["action"]["reason"]


@pytest.mark.asyncio
async def test_duplicate_request_is_one_run_even_when_concurrent(stack):
    _database, repository, _queue, manager, _worker = stack
    accepted = await asyncio.gather(
        *(manager.create_run(request_id="same-request", alert=alert()) for _ in range(8))
    )
    assert len({item.run_id for item in accepted}) == 1
    async with repository.sessions() as session:
        from sqlalchemy import func, select

        from opspilot.persistence.models import RunRecord

        assert await session.scalar(select(func.count()).select_from(RunRecord)) == 1


@pytest.mark.asyncio
async def test_illegal_state_transition_is_rejected(stack):
    _database, repository, _queue, manager, worker = stack
    created = await manager.create_run(request_id="transition-request", alert=alert())
    await worker.process_once(timeout_seconds=0.01)
    with pytest.raises(InvalidStateTransition, match="illegal run transition"):
        await repository.transition(created.run_id, RunStatus.SUCCEEDED, RunStatus.RUNNING)


@pytest.mark.asyncio
async def test_removed_api_is_unavailable_and_report_is_compact(stack):
    _database, _repository, queue, manager, worker = stack
    app = create_app(task_manager=manager)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert all("/analyze" not in path for path in app.openapi()["paths"])
        for method, path in [("POST", "/api/v1/analyze"), ("GET", "/api/v1/analyze/old/status"),
                             ("GET", "/api/v1/analyze/old/result")]:
            response = await client.request(method, path, json=alert().model_dump(mode="json"))
            assert response.status_code == 404
        assert not queue.messages
        accepted = await client.post("/api/v1/runs", json={"request_id": "compact", "alert": alert().model_dump(mode="json")})
        run_id = accepted.json()["run_id"]
        await worker.process_once(timeout_seconds=.01)
        report = (await client.get(f"/api/v1/runs/{run_id}/result")).json()["report"]
        assert not {"dimension_results", "expert_results", "algorithm_signals", "matched_rules", "tool_executions"} & report.keys()
        events = (await client.get(f"/api/v1/runs/{run_id}/events")).json()
        tools = [event["detail"]["tool"] for event in events if event["event_type"] == "investigation.tool.completed"]
        assert tools and all("attempt" in item and "latency_ms" in item for item in tools)
        after = events[-2]["sequence"]
        assert (await client.get(f"/api/v1/runs/{run_id}/events?after={after}")).json() == events[-1:]


@pytest.mark.asyncio
async def test_offline_wrapper_and_runtime_use_the_same_report_builder(stack):
    from opspilot.graph import OpsPilotWorkflow

    _database, _repository, _queue, manager, worker = stack
    accepted = await manager.create_run(request_id="shared-report", alert=alert())
    await worker.process_once(timeout_seconds=.01)
    persisted = (await manager.get_result(accepted.run_id)).report
    offline = await OpsPilotWorkflow(build_default_registry(), execution_mode="sequential").analyze(
        alert(), trace_id=accepted.run_id,
    )
    ignored = {"latency_ms", "started_at", "finished_at"}
    assert persisted.model_dump(exclude=ignored) == offline.model_dump(exclude=ignored)
