"""Application service used by both the new Run API and legacy adapter."""

from __future__ import annotations

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunAccepted, RunResult, RunStatus, RuntimeEventView, RunView
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.queue import RunQueue


class TaskManager:
    def __init__(self, repository: RuntimeRepository, queue: RunQueue, settings: RuntimeSettings) -> None:
        self.repository = repository
        self.queue = queue
        self.settings = settings

    async def create_run(self, *, request_id: str, alert: AlertEvent) -> RunAccepted:
        run, created = await self.repository.create_or_get_run(
            request_id=request_id,
            alert=alert,
            graph_version=self.settings.graph_version,
            config_version=self.settings.config_version,
        )
        # The unique DB insert is the enqueue ownership decision. Retried HTTP
        # requests must not amplify queue messages; stale QUEUED repair handles
        # the rare DB-commit / Redis-write gap.
        if created:
            await self.queue.enqueue(run.run_id)
        return RunAccepted(run_id=run.run_id, request_id=run.request_id, status=RunStatus(run.status))

    async def get_run(self, run_id: str) -> RunView | None:
        run = await self.repository.get_run(run_id)
        if run is None:
            return None
        return RunView(
            run_id=run.run_id,
            request_id=run.request_id,
            alert_id=run.alert_id,
            status=RunStatus(run.status),
            current_step=run.current_step,
            attempt=run.attempt,
            graph_version=run.graph_version,
            config_version=run.config_version,
            recovered_count=run.recovered_count,
            last_error_code=run.last_error_code,
            last_error_message=run.last_error_message,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    async def get_result(self, run_id: str) -> RunResult | None:
        run = await self.repository.get_run(run_id)
        if run is None:
            return None
        return RunResult(
            run_id=run_id,
            status=RunStatus(run.status),
            report=await self.repository.get_report(run_id),
        )

    async def get_events(self, run_id: str, *, after: int = 0) -> list[RuntimeEventView] | None:
        if await self.repository.get_run(run_id) is None:
            return None
        rows = await self.repository.list_events(run_id, after=after)
        return [
            RuntimeEventView(
                sequence=row.sequence,
                event_type=row.event_type,
                status=row.status,
                step_name=row.step_name,
                detail=row.detail_json,
                created_at=row.created_at,
            )
            for row in rows
        ]
