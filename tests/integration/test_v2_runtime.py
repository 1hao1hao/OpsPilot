from datetime import UTC, datetime

from sqlalchemy import select

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunStatus
from opspilot.persistence import Database
from opspilot.persistence.models import ToolExecutionRecord
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.queue import InMemoryRunQueue
from opspilot.runtime.task_manager import TaskManager
from opspilot.runtime.worker import RuntimeWorker
from opspilot.tools.registry import build_default_registry


async def test_v2_graph_runs_through_worker_and_persists_task_graph(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'v2-runtime.db'}")
    await database.create_schema()
    settings = RuntimeSettings(investigation_mode="v2", investigation_max_rounds=3,
                               investigation_max_tool_calls=10)
    repository = RuntimeRepository(database.sessions)
    queue = InMemoryRunQueue()
    alert = AlertEvent(
        alert_id="v2-runtime", service_name="checkout", alert_type="timeout", severity="P1",
        timestamp=datetime(2026, 11, 14, tzinfo=UTC),
        signals={"metric": {"tp95": {"current": 1200, "baseline": 100}},
                 "trace": {"traces": [{"trace_id": "db-path", "spans": [
                     {"span_id": "root", "service": "checkout", "status": "OK"},
                     {"span_id": "db", "parent_span_id": "root", "service": "mysql",
                      "status": "ERROR", "duration_ms": 1400}]}]},
                 "db": {"replication_lag_seconds": 21}},
    )
    try:
        accepted = await TaskManager(repository, queue, settings).create_run(request_id="v2-runtime", alert=alert)
        worker = RuntimeWorker(repository, queue, RecoverableExecution(
            repository, build_default_registry(), settings), settings)
        await worker.process_once(timeout_seconds=0.01)
        run = await repository.get_run(accepted.run_id)
        assert run.status == RunStatus.SUCCEEDED.value
        report = await repository.get_report(accepted.run_id)
        assert report is not None and report.investigation is not None
        checkpoint = await repository.latest_checkpoint(accepted.run_id)
        assert checkpoint.state_json["investigation"]["task_graph"]["version"] >= 1
        assert any(task["selected_skill"] for task in
                   checkpoint.state_json["investigation"]["task_graph"]["tasks"])
        assert checkpoint.schema_version == "4.0"
    finally:
        await database.dispose()


async def test_v2_restart_reuses_checkpointed_tool_call(tmp_path):
    class SimulatedCrash(BaseException):
        pass

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'v2-recovery.db'}")
    await database.create_schema()
    settings = RuntimeSettings(investigation_mode="v2", recovery_stale_seconds=0)
    repository = RuntimeRepository(database.sessions)
    queue = InMemoryRunQueue()
    alert = AlertEvent(alert_id="v2-recovery", service_name="checkout", alert_type="timeout",
                       severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
                       signals={"metric": {"cpu_usage": 0.82}})
    try:
        accepted = await TaskManager(repository, queue, settings).create_run(request_id="v2-recovery", alert=alert)
        claimed = await repository.transition(accepted.run_id, RunStatus.QUEUED, RunStatus.RUNNING)

        def crash_before(_run_id, step_name):
            if step_name == "tool:traces.query":
                raise SimulatedCrash()

        interrupted = RecoverableExecution(repository, build_default_registry(), settings,
                                           before_step=crash_before)
        with repository.ownership(accepted.run_id, claimed.attempt):
            try:
                await interrupted.execute(accepted.run_id)
            except SimulatedCrash:
                pass
            else:
                raise AssertionError("expected crash after first persisted observation")
        checkpoint = await repository.latest_checkpoint(accepted.run_id)
        assert checkpoint.state_json["investigation"]["executed_tools"] == ["metrics.query"]
        async with database.sessions() as session:
            before = await session.scalar(select(ToolExecutionRecord).where(
                ToolExecutionRecord.run_id == accepted.run_id,
                ToolExecutionRecord.tool_name == "metrics.query"))
            before_attempt = before.attempt
            call_id = before.tool_call_id
        assert await repository.recover_stale_runs(0, max_attempts=3) == [accepted.run_id]
        await repository.requeue_recovered(accepted.run_id)
        worker = RuntimeWorker(repository, queue, RecoverableExecution(
            repository, build_default_registry(), settings), settings)
        await worker.process_once(timeout_seconds=0.01)
        assert (await repository.get_run(accepted.run_id)).status == RunStatus.SUCCEEDED.value
        after = await repository.get_tool_execution(call_id)
        assert after.attempt == before_attempt
    finally:
        await database.dispose()
