from opspilot.investigation.tasks import TaskStatus, reconcile_plan
from opspilot.models import EvidenceRequirement, ObservationQuality
from opspilot.skills import SkillCatalog


def requirement(identity, service, signal, tools, *, satisfied=False, active=True, blocking=True):
    return EvidenceRequirement(
        requirement_id=identity, service=service, signal=signal, purpose="observed dependency",
        satisfied=satisfied, active=active, blocking=blocking,
        quality=ObservationQuality.ABNORMAL if satisfied else ObservationQuality.NO_DATA,
        suggested_tools=tools, reason="test observation",
    )


def test_new_observation_reconciles_task_graph_instead_of_only_changing_score():
    first = reconcile_plan([
        requirement("db:slow", "mysql", "db_slow_query", ["db.slowlog"]),
        requirement("rpc:timeout", "payment", "rpc_timeout_rate", ["rpc.metrics"]),
    ], [])
    assert [(item.domain, item.status) for item in first.tasks] == [
        ("db", TaskStatus.READY), ("rpc", TaskStatus.READY)]
    second = reconcile_plan([
        requirement("db:slow", "mysql", "db_slow_query", ["db.slowlog"], satisfied=True),
        requirement("kafka:lag", "broker", "kafka_consumer_lag", ["kafka.lag"]),
    ], [], first)
    assert {item.task_id: item.status for item in second.tasks} == {
        "db:slow": TaskStatus.SATISFIED,
        "rpc:timeout": TaskStatus.CANCELLED,
        "kafka:lag": TaskStatus.READY,
    }
    assert second.tasks[0].target_service == "broker"
    assert second.version == first.version + 1


def test_missing_tool_is_blocked_without_fake_completion():
    plan = reconcile_plan([requirement("completion", "checkout", "completion", [])], [])
    assert plan.tasks[0].status == TaskStatus.BLOCKED
    assert plan.tasks[0].result_observation_ids == []


def test_skill_loads_on_demand_and_changes_tool_order_without_granting_access():
    catalog = SkillCatalog()
    assert catalog.metadata and not catalog.activations
    req = requirement("db:latency", "mysql", "db_slow_query",
                      ["db.replication", "db.connections", "db.slowlog"])
    plain = reconcile_plan([req], [])
    skilled = reconcile_plan([req], [], skills=catalog)
    assert plain.tasks[0].allowed_tools[0] == "db.replication"
    assert skilled.tasks[0].allowed_tools[0] == "db.slowlog"
    assert skilled.tasks[0].selected_skill == "db-bottleneck-triage"
    assert skilled.tasks[0].skill_version == catalog.activations[0].version
    assert skilled.tasks[0].skill_loaded_bytes == catalog.activations[0].loaded_bytes
    assert skilled.tasks[0].skill_tool_order[0] == "db.slowlog"
    assert catalog.activations[0].loaded_bytes > 0
    restricted = reconcile_plan([requirement("db:restricted", "mysql", "db_slow_query",
                                             ["db.connections"])], [], skills=catalog)
    assert restricted.tasks[0].allowed_tools == ["db.connections"]


def test_missing_skill_body_falls_back_to_authorized_tool_order(tmp_path):
    folder = tmp_path / "db-bottleneck-triage"
    folder.mkdir()
    skill = folder / "SKILL.md"
    skill.write_text("---\nname: db-bottleneck-triage\ndescription: db triage\n---\n", encoding="utf-8")
    catalog = SkillCatalog(tmp_path)
    skill.unlink()
    req = requirement("db:fallback", "mysql", "db_slow_query", ["db.replication", "db.slowlog"])
    plan = reconcile_plan([req], [], skills=catalog)
    assert plan.tasks[0].selected_skill is None
    assert plan.tasks[0].allowed_tools == ["db.replication", "db.slowlog"]


def test_skill_changes_authorized_fallback_measurement_after_direct_tool_fails():
    req = requirement("db:slow-fallback", "mysql", "db_slow_query", ["db.slowlog"])
    capabilities = {"db": ["db.replication", "db.slowlog", "db.connections"]}
    plain = reconcile_plan([req], [], capabilities=capabilities)
    skilled = reconcile_plan([req], [], skills=SkillCatalog(), capabilities=capabilities)
    assert plain.tasks[0].allowed_tools[:2] == ["db.slowlog", "db.replication"]
    assert skilled.tasks[0].allowed_tools[:2] == ["db.slowlog", "db.connections"]
