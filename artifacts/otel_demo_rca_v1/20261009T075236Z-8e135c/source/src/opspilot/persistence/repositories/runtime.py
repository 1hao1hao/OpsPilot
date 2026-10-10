"""Transactional repository and the only writer of runtime state."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from opspilot.models import AlertEvent, DiagnosisReport, RunStatus, StepStatus, ToolExecutionStatus
from opspilot.persistence.models import (
    CheckpointRecord,
    DiagnosisReportRecord,
    RunRecord,
    RuntimeEventRecord,
    StepRecord,
    ToolExecutionRecord,
)


class InvalidStateTransition(RuntimeError):
    """Raised when a stale or illegal actor attempts to change a run."""


ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING},
    RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.RETRYING, RunStatus.CANCELLED},
    RunStatus.RETRYING: {RunStatus.QUEUED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


class RuntimeRepository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def create_or_get_run(
        self,
        *,
        request_id: str,
        alert: AlertEvent,
        graph_version: str,
        config_version: str,
        run_id: str | None = None,
    ) -> tuple[RunRecord, bool]:
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        record = RunRecord(
            run_id=run_id,
            request_id=request_id,
            alert_id=alert.alert_id,
            alert_json=alert.model_dump(mode="json"),
            status=RunStatus.QUEUED.value,
            graph_version=graph_version,
            config_version=config_version,
        )
        try:
            async with self.sessions.begin() as session:
                session.add(record)
                await session.flush()
                await self._append_event(session, record.run_id, "run.created", status=RunStatus.QUEUED.value)
            return record, True
        except IntegrityError:
            async with self.sessions() as session:
                existing = await session.scalar(select(RunRecord).where(RunRecord.request_id == request_id))
                if existing is None:  # pragma: no cover - defensive for unrelated constraints
                    raise
                return existing, False

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self.sessions() as session:
            return await session.get(RunRecord, run_id)

    async def get_report(self, run_id: str) -> DiagnosisReport | None:
        async with self.sessions() as session:
            row = await session.get(DiagnosisReportRecord, run_id)
            return DiagnosisReport.model_validate(row.report_json) if row else None

    async def list_events(self, run_id: str, *, after: int = 0) -> list[RuntimeEventRecord]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(RuntimeEventRecord)
                .where(RuntimeEventRecord.run_id == run_id, RuntimeEventRecord.sequence > after)
                .order_by(RuntimeEventRecord.sequence)
            )
            return list(result)

    async def append_runtime_event(
        self,
        run_id: str,
        event_type: str,
        *,
        detail: dict | None = None,
        status: str | None = None,
        step_name: str | None = None,
    ) -> None:
        """Expose structured investigation decisions without bypassing the repository writer."""
        async with self.sessions.begin() as session:
            await self._append_event(
                session,
                run_id,
                event_type,
                detail=detail,
                status=status,
                step_name=step_name,
            )

    async def transition(self, run_id: str, expected: RunStatus, target: RunStatus, **values) -> RunRecord:
        if target not in ALLOWED_TRANSITIONS[expected]:
            raise InvalidStateTransition(f"illegal run transition {expected.value} -> {target.value}")
        now = datetime.now(UTC)
        values.update(status=target.value, updated_at=now)
        if target == RunStatus.RUNNING:
            values.setdefault("started_at", now)
            values.setdefault("attempt", RunRecord.attempt + 1)
        if target in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            values.setdefault("finished_at", now)
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(RunRecord)
                .where(RunRecord.run_id == run_id, RunRecord.status == expected.value)
                .values(**values)
                .returning(RunRecord)
            )
            record = result.scalar_one_or_none()
            if record is None:
                current = await session.get(RunRecord, run_id)
                actual = current.status if current else "missing"
                raise InvalidStateTransition(
                    f"run {run_id} expected {expected.value}, found {actual}; target={target.value}"
                )
            await self._append_event(session, run_id, "run.transition", status=target.value)
            return record

    async def begin_step(self, run_id: str, step_name: str, execution_version: str) -> StepRecord:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(StepRecord).where(
                    StepRecord.run_id == run_id,
                    StepRecord.step_name == step_name,
                    StepRecord.execution_version == execution_version,
                )
            )
            if existing and existing.status == StepStatus.SUCCEEDED.value:
                return existing
            if existing:
                existing.status = StepStatus.RUNNING.value
                existing.attempt += 1
                existing.started_at = now
                existing.finished_at = None
                step = existing
            else:
                run = await session.get(RunRecord, run_id)
                step = StepRecord(
                    step_id=f"step-{uuid.uuid4().hex}",
                    run_id=run_id,
                    step_name=step_name,
                    execution_version=execution_version,
                    status=StepStatus.RUNNING.value,
                    attempt=run.attempt,
                )
                session.add(step)
            await session.execute(
                update(RunRecord).where(RunRecord.run_id == run_id).values(current_step=step_name, updated_at=now)
            )
            await self._append_event(session, run_id, "step.started", status=StepStatus.RUNNING.value, step_name=step_name)
            return step

    async def complete_step_with_checkpoint(
        self,
        *,
        run_id: str,
        step_name: str,
        execution_version: str,
        output: dict,
        state: dict,
        schema_version: str,
        graph_version: str,
    ) -> CheckpointRecord:
        """Atomically commit successful step output and its recovery checkpoint."""
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            step = await session.scalar(
                select(StepRecord).where(
                    StepRecord.run_id == run_id,
                    StepRecord.step_name == step_name,
                    StepRecord.execution_version == execution_version,
                )
            )
            if step is None:
                raise RuntimeError(f"step not started: {step_name}")
            step.status = StepStatus.SUCCEEDED.value
            step.output_json = output
            step.output_ref = f"checkpoint:{run_id}:{step_name}"
            step.finished_at = now
            checkpoint = CheckpointRecord(
                checkpoint_id=f"cp-{uuid.uuid4().hex}",
                run_id=run_id,
                completed_step=step_name,
                state_json=state,
                schema_version=schema_version,
                graph_version=graph_version,
                created_at=now,
            )
            session.add(checkpoint)
            await session.execute(update(RunRecord).where(RunRecord.run_id == run_id).values(updated_at=now))
            await self._append_event(
                session, run_id, "checkpoint.saved", status=StepStatus.SUCCEEDED.value, step_name=step_name
            )
            return checkpoint

    async def latest_checkpoint(self, run_id: str) -> CheckpointRecord | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(CheckpointRecord)
                .where(CheckpointRecord.run_id == run_id)
                .order_by(CheckpointRecord.created_at.desc(), CheckpointRecord.checkpoint_id.desc())
                .limit(1)
            )

    async def save_report_and_succeed(self, run_id: str, report: DiagnosisReport) -> None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(RunRecord)
                .where(RunRecord.run_id == run_id, RunRecord.status == RunStatus.RUNNING.value)
                .values(status=RunStatus.SUCCEEDED.value, current_step="report", updated_at=now, finished_at=now)
            )
            if result.rowcount != 1:
                raise InvalidStateTransition(f"run {run_id} is not RUNNING when report is committed")
            session.add(DiagnosisReportRecord(run_id=run_id, report_json=report.model_dump(mode="json")))
            await self._append_event(session, run_id, "run.succeeded", status=RunStatus.SUCCEEDED.value)

    async def fail_run(self, run_id: str, error_code: str, message: str) -> None:
        run = await self.get_run(run_id)
        if run is None or RunStatus(run.status) in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return
        expected = RunStatus(run.status)
        await self.transition(
            run_id,
            expected,
            RunStatus.FAILED,
            last_error_code=error_code,
            last_error_message=message,
        )

    async def recover_stale_runs(self, stale_seconds: float) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
        recovered: list[str] = []
        async with self.sessions.begin() as session:
            rows = list(
                await session.scalars(
                    select(RunRecord).where(
                        RunRecord.status == RunStatus.RUNNING.value,
                        RunRecord.updated_at <= cutoff,
                    )
                )
            )
            for run in rows:
                run.status = RunStatus.RETRYING.value
                run.recovered_count += 1
                run.updated_at = datetime.now(UTC)
                await self._append_event(
                    session,
                    run.run_id,
                    "run.recovered",
                    status=RunStatus.RETRYING.value,
                    detail={"recovered_count": run.recovered_count},
                )
                recovered.append(run.run_id)
        return recovered

    async def list_stale_queued_runs(self, stale_seconds: float) -> list[str]:
        """Find DB facts that may have lost their post-commit queue write."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(RunRecord.run_id).where(
                        RunRecord.status == RunStatus.QUEUED.value,
                        RunRecord.updated_at <= cutoff,
                    )
                )
            )

    async def requeue_recovered(self, run_id: str) -> None:
        await self.transition(run_id, RunStatus.RETRYING, RunStatus.QUEUED)

    async def get_tool_execution(self, tool_call_id: str) -> ToolExecutionRecord | None:
        async with self.sessions() as session:
            return await session.get(ToolExecutionRecord, tool_call_id)

    async def start_tool_execution(
        self, *, tool_call_id: str, run_id: str, step_name: str, tool_name: str
    ) -> ToolExecutionRecord:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            row = await session.get(ToolExecutionRecord, tool_call_id)
            if row is None:
                row = ToolExecutionRecord(
                    tool_call_id=tool_call_id,
                    run_id=run_id,
                    step_name=step_name,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.RUNNING.value,
                    attempt=1,
                    started_at=now,
                )
                session.add(row)
            elif row.status != ToolExecutionStatus.SUCCEEDED.value:
                row.status = ToolExecutionStatus.RUNNING.value
                row.attempt += 1
                row.started_at = now
            await self._append_event(
                session,
                run_id,
                "tool.started",
                status=row.status,
                step_name=step_name,
                detail={"tool_call_id": tool_call_id, "tool_name": tool_name, "attempt": row.attempt},
            )
            return row

    async def finish_tool_execution(self, tool_call_id: str, result: dict) -> None:
        now = datetime.now(UTC)
        status = (
            ToolExecutionStatus.SUCCEEDED.value if result["status"] == "success" else ToolExecutionStatus.FAILED.value
        )
        async with self.sessions.begin() as session:
            row = await session.get(ToolExecutionRecord, tool_call_id)
            if row is None:
                raise RuntimeError(f"tool execution not started: {tool_call_id}")
            row.status = status
            row.result_json = result
            row.error_code = result.get("error_code")
            row.error_message = result.get("error_message")
            row.latency_ms = result.get("latency_ms")
            row.attempt = max(row.attempt, int(result.get("attempt", 1)))
            row.finished_at = now
            await self._append_event(
                session,
                row.run_id,
                "tool.finished",
                status=status,
                step_name=row.step_name,
                detail={"tool_call_id": tool_call_id, "attempt": row.attempt, "error_code": row.error_code},
            )

    async def _append_event(
        self,
        session,
        run_id: str,
        event_type: str,
        *,
        status: str | None = None,
        step_name: str | None = None,
        detail: dict | None = None,
    ) -> None:
        current = await session.scalar(
            select(func.coalesce(func.max(RuntimeEventRecord.sequence), 0)).where(RuntimeEventRecord.run_id == run_id)
        )
        session.add(
            RuntimeEventRecord(
                event_id=f"evt-{uuid.uuid4().hex}",
                run_id=run_id,
                sequence=int(current) + 1,
                event_type=event_type,
                status=status,
                step_name=step_name,
                detail_json=detail or {},
            )
        )
