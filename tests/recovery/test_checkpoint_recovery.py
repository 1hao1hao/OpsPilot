from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, update

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunStatus, ToolExecutionStatus
from opspilot.persistence import Database
from opspilot.persistence.models import CheckpointRecord, StepRecord, ToolExecutionRecord
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.errors import WorkerCrash
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.queue import InMemoryRunQueue
from opspilot.runtime.task_manager import TaskManager
from opspilot.runtime.worker import RuntimeWorker
from opspilot.tools import ToolDefinition, ToolRegistry
from opspilot.tools.errors import ToolExecutionError
from opspilot.tools.registry import AlertToolInput, ObservationOutput, build_default_registry


def make_alert() -> AlertEvent:
    return AlertEvent(
        alert_id="recover-db-alert",
        service_name="payment-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        signals={"db": {"active_connections": 198, "max_connections": 200}},
    )


class CountingProvider:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    async def read(self, signal_key: str, alert: AlertEvent) -> dict:
        self.calls[signal_key] += 1
        return alert.signals.get(signal_key, {})


@pytest.fixture
async def runtime(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    await database.create_schema()
    repository = RuntimeRepository(database.sessions)
    queue = InMemoryRunQueue()
    settings = RuntimeSettings(database_url="sqlite+aiosqlite://", recovery_stale_seconds=0)
    manager = TaskManager(repository, queue, settings)
    yield database, repository, queue, settings, manager
    await database.dispose()


@pytest.mark.asyncio
async def test_worker_crash_after_adaptive_db_checkpoint_resumes_without_repeating_tool(runtime):
    database, repository, queue, settings, manager = runtime
    provider = CountingProvider()
    registry = build_default_registry(provider=provider)

    def crash_after_db(_run_id: str, step_name: str) -> None:
        if step_name == "tool:db.connections":
            raise WorkerCrash("simulated process termination")

    first = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, registry, settings, after_checkpoint=crash_after_db),
        settings,
    )
    accepted = await manager.create_run(request_id="recover-once", alert=make_alert())
    with pytest.raises(WorkerCrash):
        await first.process_once(timeout_seconds=0.01)
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.RUNNING
    assert provider.calls["db"] == 1

    second = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, registry, settings),
        settings,
    )
    assert await second.recover_once() == [accepted.run_id]
    await second.process_once(timeout_seconds=0.01)
    recovered = await manager.get_run(accepted.run_id)
    assert recovered.status == RunStatus.SUCCEEDED
    assert recovered.recovered_count == 1
    assert provider.calls["db"] == 1
    async with database.sessions() as session:
        success_count = await session.scalar(
            select(func.count()).select_from(ToolExecutionRecord).where(
                ToolExecutionRecord.run_id == accepted.run_id,
                ToolExecutionRecord.tool_name == "db.connections",
                ToolExecutionRecord.status == ToolExecutionStatus.SUCCEEDED.value,
            )
        )
        assert success_count == 1


@pytest.mark.asyncio
async def test_duplicate_terminal_delivery_is_noop(runtime):
    database, repository, queue, settings, manager = runtime
    worker = RuntimeWorker(repository, queue, RecoverableExecution(repository, build_default_registry(), settings), settings)
    accepted = await manager.create_run(request_id="duplicate-terminal", alert=make_alert())
    await worker.process_once(timeout_seconds=0.01)
    async with database.sessions() as session:
        before = await session.scalar(
            select(func.count()).select_from(StepRecord).where(StepRecord.run_id == accepted.run_id)
        )
    await queue.enqueue(accepted.run_id)
    assert await worker.process_once(timeout_seconds=0.01)
    async with database.sessions() as session:
        after = await session.scalar(
            select(func.count()).select_from(StepRecord).where(StepRecord.run_id == accepted.run_id)
        )
    assert after == before


@pytest.mark.asyncio
async def test_incompatible_checkpoint_fails_with_diagnostic(runtime):
    database, repository, queue, settings, manager = runtime

    def crash_after_plan(_run_id: str, step_name: str) -> None:
        if step_name == "tool:metrics.query":
            raise WorkerCrash

    first = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, build_default_registry(), settings, after_checkpoint=crash_after_plan),
        settings,
    )
    accepted = await manager.create_run(request_id="bad-checkpoint", alert=make_alert())
    with pytest.raises(WorkerCrash):
        await first.process_once(timeout_seconds=0.01)
    async with database.sessions.begin() as session:
        await session.execute(
            update(CheckpointRecord)
            .where(CheckpointRecord.run_id == accepted.run_id)
            .values(schema_version="999")
        )
    second = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, build_default_registry(), settings),
        settings,
    )
    await second.recover_once()
    await second.process_once(timeout_seconds=0.01)
    run = await manager.get_run(accepted.run_id)
    assert run.status == RunStatus.FAILED
    assert run.last_error_code == "checkpoint_version_mismatch"
    assert "schema=999" in run.last_error_message


def one_tool_registry(handler, *, max_attempts: int = 3, timeout_seconds: float = 0.02) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="db.inspect",
            version="1",
            description="controlled DB tool",
            input_schema=AlertToolInput,
            output_schema=ObservationOutput,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            handler=handler,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_retryable_error_retries_and_permanent_error_does_not(runtime):
    _database, repository, queue, settings, manager = runtime
    retry_calls = 0

    async def retryable(payload: AlertToolInput) -> ObservationOutput:
        nonlocal retry_calls
        retry_calls += 1
        if retry_calls == 1:
            raise ToolExecutionError("temporary")
        return ObservationOutput(observations=payload.alert.signals["db"])

    first = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, one_tool_registry(retryable), settings),
        settings,
    )
    retry_run = await manager.create_run(request_id="retryable", alert=make_alert())
    await first.process_once(timeout_seconds=0.01)
    assert retry_calls == 2
    assert (await manager.get_result(retry_run.run_id)).report.tool_executions[0].attempt == 2

    permanent_calls = 0

    async def permanent(_payload: AlertToolInput) -> ObservationOutput:
        nonlocal permanent_calls
        permanent_calls += 1
        raise ValueError("permanent invalid provider response")

    second = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, one_tool_registry(permanent), settings),
        settings,
    )
    permanent_run = await manager.create_run(request_id="permanent", alert=make_alert())
    await second.process_once(timeout_seconds=0.01)
    assert permanent_calls == 1
    report = (await manager.get_result(permanent_run.run_id)).report
    assert report.degraded is True
    assert report.tool_executions[0].attempt == 1


@pytest.mark.asyncio
async def test_tool_timeout_is_persisted_and_bounded(runtime):
    _database, repository, queue, settings, manager = runtime
    calls = 0

    async def slow(_payload: AlertToolInput) -> ObservationOutput:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return ObservationOutput(observations={})

    worker = RuntimeWorker(
        repository,
        queue,
        RecoverableExecution(repository, one_tool_registry(slow, max_attempts=2, timeout_seconds=0.005), settings),
        settings,
    )
    accepted = await manager.create_run(request_id="timeout", alert=make_alert())
    await worker.process_once(timeout_seconds=0.01)
    report = (await manager.get_result(accepted.run_id)).report
    assert calls == 2
    assert report.degraded is True
    assert report.tool_executions[0].error_code == "tool_timeout"
    assert report.tool_executions[0].attempt == 2
