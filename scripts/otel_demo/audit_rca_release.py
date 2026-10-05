"""Independently audit frozen configuration, paired inputs and engine isolation."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from opspilot.llm import DeepSeekSecrets
from opspilot.otel_benchmark.experiment import assert_frozen
from opspilot.otel_benchmark.experiment_report import completed_records
from opspilot.otel_benchmark.isolation import LeakageGuard
from opspilot.otel_benchmark.modes import MODES, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    assert read_json(run / "status.json")["status"] == "completed"
    assert_frozen(run)
    flags = read_json(run / "final/flags.json")["flags"]
    assert all(c["defaultVariant"] == "off" for c in flags.values() if "off" in c["variants"])
    containers = read_json(run / "final/containers.json")
    assert len(containers) == 28
    assert all(c["state"] == "running" and c["health"] not in ("starting", "unhealthy") for c in containers)
    guard = LeakageGuard(flags)
    records = completed_records(read_json(run / "records.json"))
    pairs, scanned = {}, 0
    for record in records:
        directory = Path(record["path"])
        alert = read_json(directory / "alert.json")
        pairs.setdefault((record["scenario_id"], record["repetition"]), []).append(alert)
        for relative in ("alert.json", "rca/tool_arguments.json", "rca/tool_results.json",
                         "rca/evidence.json", "rca/report.json", "rca/events.json"):
            guard.assert_clean(read_json(directory / relative))
            scanned += 1
        api_files = list((directory / "llm").glob("api-*.json"))
        for api_file in api_files:
            guard.assert_clean(read_json(api_file)["request"])
            scanned += 1
        events = read_json(directory / "rca/events.json")
        score = record["score"]
        assert len(events["executed_tools"]) == score["tool_calls"]
        assert events["expert_budget_used"] == score["expert_calls"]
        assert len(api_files) == record["llm"]["api_calls"]
        if record["mode"] == "fixed_full":
            assert score["tool_calls"] == 13 and score["expert_calls"] == 0 and not api_files
        elif record["mode"] == "adaptive_no_l2":
            assert score["expert_calls"] == 0
    assert len(records) == 63 and len(pairs) == 21
    assert all(len(alerts) == len(MODES) and all(a == alerts[0] for a in alerts) for alerts in pairs.values())
    secret = DeepSeekSecrets().deepseek_api_key.get_secret_value()
    secret_scanned = 0
    if secret:
        for file in run.rglob("*.json"):
            assert secret not in file.read_text(encoding="utf-8"), "Secret found in an artifact; contents withheld"
            secret_scanned += 1
    audit = {"status": "passed", "checked_at": datetime.now(UTC).isoformat(),
             "frozen_source_and_configuration_unchanged": True, "paired_alerts_identical": True,
             "actual_diagnoses": len(records), "paired_lifecycles": len(pairs),
             "engine_payloads_checked": scanned, "api_key_absent_json_files_checked": secret_scanned,
             "final_faults_off": True, "final_running_services": len(containers),
             "fixed_full_tools": 13, "fixed_full_experts": 0, "no_l2_experts": 0}
    (run / "independent_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit))


if __name__ == "__main__":
    main()
