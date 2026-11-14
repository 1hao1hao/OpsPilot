import json
import subprocess
import sys
from pathlib import Path


def test_standalone_demo_shows_two_scoped_specialists_and_distinct_plans(tmp_path):
    output = tmp_path / "demo.json"
    root = Path(__file__).parents[2]
    subprocess.run([sys.executable, "scripts/v2_demo.py", "--output", str(output)],
                   cwd=root, check=True, capture_output=True, text=True, timeout=30)
    scenarios = json.loads(output.read_text(encoding="utf-8"))
    assert len(scenarios) == 3
    fault, refuted, healthy = scenarios
    tools = fault["report"]["investigation"]["executed_tool_targets"]
    assert any(tool.startswith("db.") and tool.endswith("|mysql") for tool in tools)
    assert any(tool.startswith("redis.") and tool.endswith("|redis") for tool in tools)
    source_services = {evidence["service"] for evidence in fault["report"]["evidence"]}
    assert {"mysql", "redis"} <= source_services
    fault_plan = next(event["tasks"] for event in fault["events"] if event["event"] == "investigation.gate")
    healthy_plan = next(event["tasks"] for event in healthy["events"] if event["event"] == "investigation.gate")
    assert fault_plan != healthy_plan
    refuted_tools = refuted["report"]["investigation"]["executed_tool_targets"]
    assert "db.replication|mysql" in refuted_tools
    assert any(item["evidence_type"] == "redis.memory_usage" for item in refuted["report"]["evidence"])
    assert not any(item["evidence_type"] == "db.replication_lag" for item in refuted["report"]["evidence"])
    gates = [event for event in refuted["events"] if event["event"] == "investigation.gate"]
    def status(event, identity):
        return next(task["status"] for task in event["tasks"] if task["id"] == identity)

    assert status(gates[0], "dependency:db:mysql") == "READY"
    assert status(gates[1], "dependency:db:mysql") == "SATISFIED"
    assert any(observation["tool_name"] == "db.replication" and observation["quality"] == "normal"
               for observation in refuted["report"]["decision"]["observations"])
