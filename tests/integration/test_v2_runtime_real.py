"""Opt-in V2 recovery check against PostgreSQL and Redis Streams."""

import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from opspilot.config import RuntimeSettings
from opspilot.api.app import create_app
from opspilot.models import AlertEvent, RunStatus, ToolExecutionStatus
from opspilot.persistence import Database
from opspilot.persistence.models import ToolExecutionRecord
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.queue import RedisRunQueue
from opspilot.runtime.task_manager import TaskManager
from opspilot.runtime.worker import RuntimeWorker
from opspilot.tools.registry import build_default_registry

DATABASE_URL = os.getenv("OPSPILOT_TEST_DATABASE_URL")
REDIS_URL = os.getenv("OPSPILOT_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL or not REDIS_URL,
                                reason="real PostgreSQL/Redis URLs not configured")


@pytest.mark.asyncio
async def test_v2_partial_specialist_crash_recovers_without_repeating_committed_tools():
    class SimulatedCrash(BaseException):
        pass

    settings = RuntimeSettings(
        database_url=DATABASE_URL, redis_url=REDIS_URL,
        queue_name=f"opspilot:v2:test:{uuid.uuid4().hex}", investigation_mode="v2",
        investigation_max_rounds=3, investigation_max_tool_calls=10,
        recovery_stale_seconds=0, queue_pending_seconds=0.02,
    )
    database = Database(settings.database_url)
    repository = RuntimeRepository(database.sessions)
    first_queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name,
                                          pending_seconds=settings.queue_pending_seconds)
    second_queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name,
                                           pending_seconds=settings.queue_pending_seconds)
    alert = AlertEvent(
        alert_id=f"v2-crash-{uuid.uuid4().hex}", service_name="checkout", alert_type="timeout",
        severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
        signals={"metric": {"tp95": {"current": 1200, "baseline": 100}},
                 "trace": {"traces": [{"trace_id": "db-path", "spans": [
                     {"span_id": "root", "service": "checkout", "status": "OK"},
                     {"span_id": "db", "parent_span_id": "root", "service": "mysql",
                      "status": "ERROR", "duration_ms": 1400}]}]},
                 "db": {"replication_lag_seconds": 21}},
    )
    try:
        accepted = await TaskManager(repository, first_queue, settings).create_run(
            request_id=alert.alert_id, alert=alert)
        crashed = False

        def crash_after(_run_id, step_name):
            nonlocal crashed
            if not crashed and step_name.startswith("tool:") and step_name.split(":", 1)[1].split(".")[0] in {
                "db", "redis", "kafka", "rpc",
            }:
                crashed = True
                raise SimulatedCrash()

        first = RuntimeWorker(repository, first_queue, RecoverableExecution(
            repository, build_default_registry(), settings, after_checkpoint=crash_after), settings)
        with pytest.raises(SimulatedCrash):
            await first.process_once(timeout_seconds=0.01)
        assert crashed
        assert (await repository.get_run(accepted.run_id)).status == RunStatus.RUNNING.value
        checkpoint = await repository.latest_checkpoint(accepted.run_id)
        assert checkpoint.state_json["investigation"]["task_graph"]["version"] >= 1
        async with database.sessions() as session:
            before = list(await session.scalars(select(ToolExecutionRecord).where(
                ToolExecutionRecord.run_id == accepted.run_id,
                ToolExecutionRecord.status == ToolExecutionStatus.SUCCEEDED.value)))
        assert before
        attempts = {row.tool_call_id: row.attempt for row in before}

        second = RuntimeWorker(repository, second_queue, RecoverableExecution(
            repository, build_default_registry(), settings), settings)
        assert accepted.run_id in await second.recover_once()
        for _ in range(5):
            await second.process_once(timeout_seconds=0.01)
            if (await repository.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED.value:
                break
        assert (await repository.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED.value
        for call_id, attempt in attempts.items():
            assert (await repository.get_tool_execution(call_id)).attempt == attempt
        final = await repository.latest_checkpoint(accepted.run_id)
        assert final.state_json["investigation"]["evidence_board"]
    finally:
        await first_queue.client.delete(settings.queue_name)
        await first_queue.close()
        await second_queue.close()
        await database.dispose()


@pytest.mark.asyncio
async def test_v2_real_api_queue_worker_result_chain():
    settings = RuntimeSettings(
        database_url=DATABASE_URL, redis_url=REDIS_URL,
        queue_name=f"opspilot:v2:api:{uuid.uuid4().hex}", investigation_mode="v2",
    )
    database = Database(settings.database_url)
    repository = RuntimeRepository(database.sessions)
    queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name)
    manager = TaskManager(repository, queue, settings)
    worker = RuntimeWorker(repository, queue, RecoverableExecution(
        repository, build_default_registry(), settings), settings)
    request_id = f"v2-api-{uuid.uuid4().hex}"
    alert = AlertEvent(
        alert_id=request_id, service_name="checkout", alert_type="timeout",
        severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
        signals={"metric": {"tp95": {"current": 1200, "baseline": 100}}},
    )
    try:
        app = create_app(task_manager=manager)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            accepted = await client.post("/api/v1/runs", json={
                "request_id": request_id, "alert": alert.model_dump(mode="json"),
            })
            assert accepted.status_code == 202
            run_id = accepted.json()["run_id"]
            assert await worker.process_once(timeout_seconds=0.01)
            response = await client.get(f"/api/v1/runs/{run_id}/result")
            assert response.status_code == 200
            assert response.json()["status"] == "SUCCEEDED"
            assert response.json()["report"]["verdict"] in {
                "CONFIRMED", "NO_FAULT", "INCONCLUSIVE",
            }
            checkpoint = await repository.latest_checkpoint(run_id)
            assert checkpoint.state_json["investigation"]["task_graph"]["version"] >= 1
    finally:
        await queue.client.delete(settings.queue_name)
        await queue.close()
        await database.dispose()
