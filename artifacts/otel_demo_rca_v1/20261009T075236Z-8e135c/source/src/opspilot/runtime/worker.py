"""Independent queue worker and stale-run recovery scanner."""

from __future__ import annotations

import argparse
import asyncio
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
        run_id = await self.queue.dequeue(timeout_seconds or self.settings.queue_poll_seconds)
        if run_id is None:
            return False
        await self.process_run(run_id)
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
            await self.repository.transition(run_id, RunStatus.QUEUED, RunStatus.RUNNING)
        except InvalidStateTransition:
            return False
        try:
            report = await self.execution.execute(run_id)
            await self.repository.save_report_and_succeed(run_id, report)
        except CheckpointVersionMismatch as exc:
            await self.repository.fail_run(run_id, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - persist normalized terminal failure
            await self.repository.fail_run(run_id, getattr(exc, "code", "runtime_execution_error"), str(exc))
        return True

    async def recover_once(self) -> list[str]:
        queued_repairs = await self.repository.list_stale_queued_runs(self.settings.queue_repair_seconds)
        run_ids = await self.repository.recover_stale_runs(self.settings.recovery_stale_seconds)
        for run_id in run_ids:
            await self.repository.requeue_recovered(run_id)
            await self.queue.enqueue(run_id)
        for run_id in queued_repairs:
            if run_id not in run_ids:
                await self.queue.enqueue(run_id)
        return run_ids

    async def run_forever(self) -> None:
        last_scan = 0.0
        while True:
            if monotonic() - last_scan >= self.settings.recovery_scan_seconds:
                await self.recover_once()
                last_scan = monotonic()
            await self.process_once()


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
    queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name)
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
