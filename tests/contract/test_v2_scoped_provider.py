from datetime import UTC, datetime

from opspilot.models import AlertEvent, ToolCall, ToolStatus
from opspilot.tools import ToolExecutor
from opspilot.tools.registry import ObservationProvider, build_default_registry


async def test_scoped_provider_reads_observed_dependency_without_leaking_root_snapshot():
    class ScopedProvider:
        supports_scoped_queries = True

        async def read(self, key, alert):
            assert alert.service_name == "mysql"
            return {"replication_lag_seconds": 21} if key == "db" else {}

        async def read_tool(self, name, alert):
            assert name == "db.replication"
            return await self.read("db", alert)

    alert = AlertEvent(alert_id="scoped", service_name="checkout", alert_type="timeout",
                       severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
                       signals={"db": {"replication_lag_seconds": 999}})
    arguments = {"alert": alert.model_dump(mode="json"), "target_service": "mysql"}
    scoped = await ToolExecutor(build_default_registry(provider=ScopedProvider())).execute(
        ToolCall(tool_call_id="scoped-call", tool_name="db.replication", arguments=arguments))
    assert scoped.status == ToolStatus.SUCCESS
    assert scoped.data == {"observations": {"replication_lag_seconds": 21}}

    snapshot_only = await ToolExecutor(build_default_registry(provider=ObservationProvider())).execute(
        ToolCall(tool_call_id="snapshot-call", tool_name="db.replication", arguments=arguments))
    assert snapshot_only.data == {"observations": {}}
