"""Opt-in tests against actual PostgreSQL, Redis and independent worker processes."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from opspilot.api.app import create_app
from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunStatus, ToolExecutionStatus
from opspilot.persistence import Database
from opspilot.persistence.models import RunRecord, ToolExecutionRecord
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.queue import RedisRunQueue
from opspilot.runtime.task_manager import TaskManager

DATABASE_URL = os.getenv("OPSPILOT_TEST_DATABASE_URL")
REDIS_URL = os.getenv("OPSPILOT_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL or not REDIS_URL, reason="real PostgreSQL/Redis URLs not configured")


def process_alert(alert_id: str) -> AlertEvent:
    return AlertEvent(
        alert_id=alert_id,
        service_name="checkout-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        signals={"db": {"replication_lag_seconds": 21}, "metric": {"cpu_usage": 0.51}},
    )


def worker_environment(settings: RuntimeSettings, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "OPSPILOT_DATABASE_URL": settings.database_url,
        "OPSPILOT_REDIS_URL": settings.redis_url,
        "OPSPILOT_QUEUE_NAME": settings.queue_name,
        "OPSPILOT_RECOVERY_STALE_SECONDS": "0",
        **extra,
    }


async def run_worker(settings: RuntimeSettings, *args: str, **extra: str) -> int:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "opspilot.runtime.worker",
        *args,
        env=worker_environment(settings, **extra),
    )
    return await process.wait()


@pytest.fixture
async def real_stack():
    queue_name = f"opspilot:test:{uuid.uuid4().hex}"
    settings = RuntimeSettings(
        database_url=DATABASE_URL,
        redis_url=REDIS_URL,
        queue_name=queue_name,
        recovery_stale_seconds=0,
    )
    database = Database(settings.database_url)
    queue = RedisRunQueue.from_url(settings.redis_url, queue_name)
    manager = TaskManager(RuntimeRepository(database.sessions), queue, settings)
    yield database, queue, manager, settings
    await queue.client.delete(queue_name)
    await queue.close()
    await database.dispose()


@pytest.mark.asyncio
async def test_real_api_to_redis_to_worker_to_postgres(real_stack):
    _database, _queue, manager, settings = real_stack
    app = create_app(task_manager=manager)
    request_id = f"real-normal-{uuid.uuid4().hex}"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/v1/runs",
            json={"request_id": request_id, "alert": process_alert(request_id).model_dump(mode="json")},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert (await client.get(f"/api/v1/runs/{run_id}")).json()["status"] == "QUEUED"
        assert await run_worker(settings, "--once") == 0
        result = await client.get(f"/api/v1/runs/{run_id}/result")
        assert result.status_code == 200
        assert result.json()["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_real_postgres_unique_request_constraint_handles_concurrency(real_stack):
    database, _queue, manager, _settings = real_stack
    request_id = f"real-idempotent-{uuid.uuid4().hex}"
    accepted = await asyncio.gather(
        *(manager.create_run(request_id=request_id, alert=process_alert(request_id)) for _ in range(8))
    )
    assert len({item.run_id for item in accepted}) == 1
    async with database.sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(RunRecord).where(RunRecord.request_id == request_id)
        )
    assert count == 1


@pytest.mark.asyncio
async def test_real_worker_process_crash_recovers_without_repeating_db_tool(real_stack):
    database, _queue, manager, settings = real_stack
    request_id = f"real-crash-{uuid.uuid4().hex}"
    accepted = await manager.create_run(request_id=request_id, alert=process_alert(request_id))
    assert await run_worker(settings, "--once", OPSPILOT_CRASH_BEFORE_STEP="tool:redis.inspect") == 97
    crashed = await manager.get_run(accepted.run_id)
    assert crashed.status == RunStatus.RUNNING

    repository = RuntimeRepository(database.sessions)
    async with database.sessions() as session:
        before = list(
            await session.scalars(
                select(ToolExecutionRecord).where(
                    ToolExecutionRecord.run_id == accepted.run_id,
                    ToolExecutionRecord.tool_name == "db.inspect",
                )
            )
        )
    # Events deliberately carry identifiers, not result payloads. The DB row is
    # the idempotency fact and must already be successful before process death.
    assert len(before) == 1
    assert before[0].status == ToolExecutionStatus.SUCCEEDED.value
    tool_call_id = before[0].tool_call_id

    assert await run_worker(settings, "--recover-once") == 0
    assert await run_worker(settings, "--once") == 0
    recovered = await manager.get_run(accepted.run_id)
    assert recovered.status == RunStatus.SUCCEEDED
    assert recovered.recovered_count >= 1
    after = await repository.get_tool_execution(tool_call_id)
    assert after.status == ToolExecutionStatus.SUCCEEDED.value
    assert after.attempt == before[0].attempt
