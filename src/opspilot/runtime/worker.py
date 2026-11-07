"""Independent queue worker and stale-run recovery scanner."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from time import monotonic

from opspilot.config import RuntimeSettings
from opspilot.models import RunStatus
from opspilot.persistence import Database
from opspilot.persistence.repositories import InvalidStateTransition, RuntimeRepository
from opspilot.runtime.errors import CheckpointVersionMismatch
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.faults import build_worker_registry
from opspilot.runtime.queue import RedisRunQueue, RunQueue

logger = logging.getLogger(__name__)


class RuntimeWorker:
    def __init__(
        self,
        repository: RuntimeRepository,
        queue: RunQueue,
        execution: RecoverableExecution,
        settings: RuntimeSettings,
    ) -> None:
        self.repository = repository
        self.queue = queue
        self.execution = execution
        self.settings = settings

    async def process_once(self, *, timeout_seconds: float | None = None) -> bool:
        delivery = await self.queue.dequeue(timeout_seconds or self.settings.queue_poll_seconds)
        if delivery is None:
            return False
        await self.process_run(delivery.run_id)
        run = await self.repository.get_run(delivery.run_id)
        if run is None or RunStatus(run.status) in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            await self.queue.ack(delivery)
        return True

    async def process_run(self, run_id: str) -> bool:
        run = await self.repository.get_run(run_id)
        if run is None or RunStatus(run.status) in {
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return False
        try:
            claimed = await self.repository.transition(run_id, RunStatus.QUEUED, RunStatus.RUNNING)
        except InvalidStateTransition:
            return False
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(run_id, claimed.attempt, stop))
        retry = False
        try:
            with self.repository.ownership(run_id, claimed.attempt):
                try:
                    report = await self.execution.execute(run_id)
                    await self.repository.save_report_and_succeed(run_id, report)
                except CheckpointVersionMismatch as exc:
                    await self.repository.fail_run(run_id, exc.code, str(exc))
                except InvalidStateTransition:
                    return False  # a newer owner has fenced this worker
                except Exception as exc:  # noqa: BLE001 - classify run-level errors
                    if getattr(exc, "retryable", False) and claimed.attempt < self.settings.run_max_attempts:
                        await self.repository.transition(
                            run_id, RunStatus.RUNNING, RunStatus.RETRYING,
                            last_error_code=getattr(exc, "code", "runtime_execution_error"),
                            last_error_message=str(exc),
                        )
                        retry = True
                    else:
                        code = "run_retry_exhausted" if getattr(exc, "retryable", False) else getattr(
                            exc, "code", "runtime_execution_error")
                        await self.repository.fail_run(run_id, code, str(exc))
        except InvalidStateTransition:
            return False  # lease was taken while handling an execution error
        finally:
            stop.set()
            await heartbeat
        if retry:
            await self.repository.requeue_recovered(run_id)
            await self.queue.enqueue(run_id)
        return True

    async def _heartbeat(self, run_id: str, attempt: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), self.settings.worker_heartbeat_seconds)
            except TimeoutError:
                try:
                    renewed = await self.repository.heartbeat(run_id, attempt)
                except Exception:
                    logger.exception("run %s heartbeat failed; lease may expire", run_id)
                    return
                if not renewed:
                    return

    async def recover_once(self) -> list[str]:
        queued_repairs = await self.repository.list_stale_queued_runs(self.settings.queue_repair_seconds)
        retrying_repairs = await self.repository.list_stale_retrying_runs(self.settings.queue_repair_seconds)
        lease = self.settings.worker_lease_seconds
        run_ids = await self.repository.recover_stale_runs(
            lease if lease is not None else self.settings.recovery_stale_seconds,
            self.settings.run_max_attempts,
        )
        for run_id in run_ids:
            try:
                await self.repository.requeue_recovered(run_id)
            except InvalidStateTransition:
                continue
            await self.queue.enqueue(run_id)
        for run_id in retrying_repairs:
            if run_id not in run_ids:
                try:
                    await self.repository.requeue_recovered(run_id)
                except InvalidStateTransition:
                    continue
                await self.queue.enqueue(run_id)
        for run_id in queued_repairs:
            if run_id not in run_ids:
                await self.queue.enqueue(run_id)
        return run_ids

    async def run_forever(self) -> None:
        last_scan = 0.0
        while True:
            try:
                if monotonic() - last_scan >= self.settings.recovery_scan_seconds:
                    await self.recover_once()
                    last_scan = monotonic()
                await self.process_once()
            except Exception:
                logger.exception("runtime worker iteration failed; retrying")
                await asyncio.sleep(self.settings.queue_poll_seconds)


def _crash_hook(_run_id: str, step_name: str) -> None:
    crash_after = os.getenv("OPSPILOT_CRASH_AFTER_STEP")
    if crash_after and step_name == crash_after:
        os._exit(97)


def _crash_before_step(_run_id: str, step_name: str) -> None:
    crash_before = os.getenv("OPSPILOT_CRASH_BEFORE_STEP")
    if crash_before and step_name == crash_before:
        os._exit(97)


async def _run(args: argparse.Namespace) -> None:
    settings = RuntimeSettings()
    database = Database(settings.database_url)
    repository = RuntimeRepository(database.sessions)
    queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name,
                                   pending_seconds=settings.queue_pending_seconds)
    execution = RecoverableExecution(
        repository,
        build_worker_registry(settings),
        settings,
        before_step=_crash_before_step,
        after_checkpoint=_crash_hook,
    )
    worker = RuntimeWorker(repository, queue, execution, settings)
    try:
        if args.recover_once:
            await worker.recover_once()
        elif args.once:
            await worker.process_once()
        else:
            await worker.run_forever()
    finally:
        await queue.close()
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsPilot recoverable runtime worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--recover-once", action="store_true", help="Scan stale runs once and exit")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
