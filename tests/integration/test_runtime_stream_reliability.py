"""Real PostgreSQL/Redis reliability checks; enabled by explicit test URLs."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunStatus
from opspilot.persistence import Database
from opspilot.persistence.models import DiagnosisReportRecord
from opspilot.persistence.repositories import InvalidStateTransition, RuntimeRepository
from opspilot.runtime.errors import RetryableRunError
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.queue import RedisRunQueue
from opspilot.runtime.task_manager import TaskManager
from opspilot.runtime.worker import RuntimeWorker
from opspilot.tools.registry import build_default_registry

DATABASE_URL = os.getenv("OPSPILOT_TEST_DATABASE_URL")
REDIS_URL = os.getenv("OPSPILOT_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL or not REDIS_URL, reason="real PostgreSQL/Redis URLs not configured")


def alert(request_id: str) -> AlertEvent:
    return AlertEvent(alert_id=request_id, service_name="checkout-service", alert_type="timeout",
                      severity="P1", timestamp=datetime(2026, 8, 12, tzinfo=UTC),
                      signals={"metric": {"cpu_usage": 0.51}})


class CountingExecution:
    def __init__(self, base, *, delay: float = 0) -> None:
        self.base = base
        self.delay = delay
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def execute(self, run_id):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return await self.base.execute(run_id)


@pytest.fixture
async def stack():
    name = f"opspilot:test:reliability:{uuid.uuid4().hex}"
    settings = RuntimeSettings(database_url=DATABASE_URL, redis_url=REDIS_URL, queue_name=name,
                               recovery_stale_seconds=0.08, queue_repair_seconds=0,
                               worker_heartbeat_seconds=0.02, queue_pending_seconds=0.02,
                               run_max_attempts=2)
    database = Database(settings.database_url)
    repository = RuntimeRepository(database.sessions)
    queue = RedisRunQueue.from_url(settings.redis_url, name, pending_seconds=settings.queue_pending_seconds)
    manager = TaskManager(repository, queue, settings)
    yield database, repository, queue, manager, settings
    await queue.client.delete(name)
    await queue.close()
    await database.dispose()


def execution(repository, settings, *, delay=0):
    base = RecoverableExecution(repository, build_default_registry(), settings)
    return CountingExecution(base, delay=delay)


@pytest.mark.asyncio
async def test_atomic_claim_duplicate_delivery_and_terminal_ack(stack):
    database, repo, queue, manager, settings = stack
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))
    await queue.enqueue(accepted.run_id)
    other_queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name, pending_seconds=0.02)
    measured = execution(repo, settings, delay=0.12)
    workers = [RuntimeWorker(repo, q, measured, settings) for q in (queue, other_queue)]
    try:
        await asyncio.gather(*(worker.process_once(timeout_seconds=0.01) for worker in workers))
        assert measured.calls == 1
        assert (await manager.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(DiagnosisReportRecord).where(
                DiagnosisReportRecord.run_id == accepted.run_id)) == 1
        await asyncio.sleep(0.03)
        await workers[1].process_once(timeout_seconds=0.01)
        assert measured.calls == 1
    finally:
        await other_queue.close()


@pytest.mark.asyncio
async def test_pending_long_run_does_not_starve_fresh_run(stack):
    _database, _repo, queue, _manager, settings = stack
    other_queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name, pending_seconds=0.02)
    try:
        await queue.enqueue("long-run")
        first = await queue.dequeue(timeout_seconds=0.01)
        assert first is not None and first.run_id == "long-run"
        await asyncio.sleep(0.03)
        await queue.enqueue("fresh-run")
        second = await other_queue.dequeue(timeout_seconds=0.01)
        assert second is not None and second.run_id == "fresh-run"
    finally:
        await other_queue.close()


@pytest.mark.asyncio
async def test_pending_recovery_does_not_starve_behind_fresh_runs(stack):
    _database, _repo, queue, _manager, settings = stack
    await queue.enqueue("crashed-run")
    first = await queue.dequeue(timeout_seconds=0.01)
    assert first is not None and first.run_id == "crashed-run"
    await asyncio.sleep(settings.queue_pending_seconds + 0.01)
    await queue.enqueue("fresh-run")
    # The same consumer gives an eligible Pending run a turn even when fresh
    # entries keep arriving.
    second = await queue.dequeue(timeout_seconds=0.01)
    assert second is not None and second.run_id == "crashed-run"
    third = await queue.dequeue(timeout_seconds=0.01)
    assert third is not None and third.run_id == "fresh-run"


@pytest.mark.asyncio
async def test_long_task_heartbeat_prevents_stale_takeover(stack):
    _database, repo, queue, manager, settings = stack
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))
    measured = execution(repo, settings)
    measured.release.clear()
    worker = RuntimeWorker(repo, queue, measured, settings)
    task = asyncio.create_task(worker.process_once(timeout_seconds=0.01))
    await measured.started.wait()
    await asyncio.sleep(0.18)
    assert await worker.recover_once() == []
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.RUNNING
    measured.release.set()
    await task
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_stale_takeover_fences_old_worker_checkpoint_and_report(stack):
    database, repo, queue, manager, settings = stack
    no_heartbeat = settings.model_copy(update={"worker_heartbeat_seconds": 5.0,
                                               "recovery_stale_seconds": 0})
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))
    old_execution = execution(repo, no_heartbeat)
    old_execution.release.clear()
    first = RuntimeWorker(repo, queue, old_execution, no_heartbeat)
    old_task = asyncio.create_task(first.process_once(timeout_seconds=0.01))
    await old_execution.started.wait()
    other_queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name, pending_seconds=0.02)
    second = RuntimeWorker(repo, other_queue, execution(repo, settings), no_heartbeat)
    try:
        assert await second.recover_once() == [accepted.run_id]
        await second.process_once(timeout_seconds=0.01)
        with repo.ownership(accepted.run_id, 1):
            with pytest.raises(InvalidStateTransition):
                await repo.complete_step_with_checkpoint(
                    run_id=accepted.run_id, step_name="report", execution_version="1",
                    output={}, state={}, schema_version=settings.checkpoint_schema_version,
                    graph_version=settings.graph_version,
                )
            with pytest.raises(InvalidStateTransition):
                await repo.save_report_and_succeed(
                    accepted.run_id, (await manager.get_result(accepted.run_id)).report)
        old_execution.release.set()
        await old_task
        run = await manager.get_run(accepted.run_id)
        assert run.status == RunStatus.SUCCEEDED
        assert run.attempt == 2
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(DiagnosisReportRecord).where(
                DiagnosisReportRecord.run_id == accepted.run_id)) == 1
    finally:
        old_execution.release.set()
        await other_queue.close()


@pytest.mark.asyncio
async def test_retryable_run_error_exhausts_run_attempts(stack):
    _database, repo, queue, manager, settings = stack
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))

    class FailingExecution:
        calls = 0

        async def execute(self, run_id):
            self.calls += 1
            raise RetryableRunError("temporary backend outage")

    failing = FailingExecution()
    worker = RuntimeWorker(repo, queue, failing, settings)
    await worker.process_once(timeout_seconds=0.01)
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.QUEUED
    await worker.process_once(timeout_seconds=0.01)
    run = await manager.get_run(accepted.run_id)
    assert (run.status, run.attempt, run.last_error_code) == (RunStatus.FAILED, 2, "run_retry_exhausted")
    assert failing.calls == 2


@pytest.mark.asyncio
async def test_stale_worker_exhausts_attempt_budget_and_pending_is_acked(stack):
    _database, repo, queue, manager, settings = stack
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))
    await repo.transition(accepted.run_id, RunStatus.QUEUED, RunStatus.RUNNING)
    assert await repo.recover_stale_runs(0, max_attempts=1) == []
    run = await manager.get_run(accepted.run_id)
    assert (run.status, run.last_error_code) == (RunStatus.FAILED, "run_retry_exhausted")
    worker = RuntimeWorker(repo, queue, execution(repo, settings), settings)
    await worker.process_once(timeout_seconds=0.01)
    assert (await queue.client.xpending(settings.queue_name, queue.group))["pending"] == 0


@pytest.mark.asyncio
async def test_ack_failure_leaves_terminal_run_safe_to_redeliver(stack):
    _database, repo, queue, manager, settings = stack
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))
    measured = execution(repo, settings)
    worker = RuntimeWorker(repo, queue, measured, settings)
    real_ack = queue.ack

    async def fail_ack(_delivery):
        raise ConnectionError("injected ACK failure")

    queue.ack = fail_ack
    with pytest.raises(ConnectionError, match="ACK failure"):
        await worker.process_once(timeout_seconds=0.01)
    queue.ack = real_ack
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED
    await asyncio.sleep(0.03)
    await worker.process_once(timeout_seconds=0.01)
    assert measured.calls == 1
    assert (await queue.client.xpending(settings.queue_name, queue.group))["pending"] == 0


@pytest.mark.asyncio
async def test_postgres_commit_redis_enqueue_gap_repaired(stack):
    _database, repo, queue, _manager, settings = stack
    manager = TaskManager(repo, queue, settings)
    real_enqueue = queue.enqueue

    async def fail_enqueue(_run_id):
        raise ConnectionError("injected Redis outage")

    queue.enqueue = fail_enqueue
    accepted = await manager.create_run(request_id=uuid.uuid4().hex, alert=alert(uuid.uuid4().hex))
    queue.enqueue = real_enqueue
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.QUEUED
    assert accepted.run_id in await repo.list_stale_queued_runs(0)
    worker = RuntimeWorker(repo, queue, execution(repo, settings), settings)
    await worker.recover_once()
    assert any(fields["run_id"] == accepted.run_id for _, fields in
               await queue.client.xrange(settings.queue_name))
    for _ in range(20):
        if (await manager.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED:
            break
        await worker.process_once(timeout_seconds=0.01)
    assert (await manager.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_legacy_list_key_requires_new_stream_name(stack):
    _database, _repo, queue, _manager, settings = stack
    await queue.client.rpush(settings.queue_name, "legacy-run-id")
    with pytest.raises(RuntimeError, match="legacy List"):
        await queue.enqueue("new-run-id")
