"""Replay captured observations and ranking; labels are used only after decisions."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from opspilot.investigation.decision import decide_diagnosis
from opspilot.models import AlertEvent, Evidence, EvidenceGateDecision, RootCauseCandidate, ToolResult


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replay(run):
    totals = defaultdict(lambda: defaultdict(int))
    rows = []
    for record in read(run / "records.json"):
        directory = Path(record["path"])
        alert_path = directory / "alert.json"
        tools_path = directory / "rca/tool_results.json"
        report_path = directory / "rca/report.json"
        alert = AlertEvent.model_validate(read(alert_path))
        tools = [ToolResult.model_validate(item) for item in read(tools_path)]
        original = read(report_path)
        evidence = [Evidence.model_validate(item) for item in original["evidence"]]
        candidates = [RootCauseCandidate.model_validate(item) for item in original["candidates"]]
        gate = EvidenceGateDecision.model_validate(original["investigation"]["gate_decisions"][-1])
        decision = decide_diagnosis(alert, tools, evidence, candidates, gate)
        # Evaluation labels never reach decide_diagnosis, the collector or planner.
        expected = record["score"]["expected"]
        top1 = candidates[0].root_cause_type.value
        fault = expected != "no_fault"
        values = totals[record["mode"]]
        values["cases"] += 1
        values["fault_cases"] += fault
        values["raw_fault_top1_hits"] += fault and top1 == expected
        values["normal_cases"] += not fault
        values["raw_normal_top1_hits"] += not fault and top1 == expected
        values["confirmed"] += decision.verdict.value == "CONFIRMED"
        values["false_confirmed"] += decision.verdict.value == "CONFIRMED" and top1 != expected
        values["no_fault"] += decision.verdict.value == "NO_FAULT"
        values["false_no_fault"] += decision.verdict.value == "NO_FAULT" and fault
        values["inconclusive"] += decision.verdict.value == "INCONCLUSIVE"
        rows.append({"scenario": record["scenario_id"], "repeat": record["repetition"],
                     "mode": record["mode"], "raw_top1": top1, "expected_for_scoring_only": expected,
                     "decision": decision.model_dump(mode="json"),
                     "inputs_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in (alert_path, tools_path, report_path)}})
    for values in totals.values():
        cases = values["cases"]
        values["raw_fault_top1_rate"] = values["raw_fault_top1_hits"] / values["fault_cases"]
        values["confirmation_rate"] = values["confirmed"] / cases
        values["false_confirmation_rate_all_cases"] = values["false_confirmed"] / cases
        values["false_confirmation_rate_among_confirmed"] = (
            values["false_confirmed"] / values["confirmed"] if values["confirmed"] else None)
        values["abstention_rate"] = values["inconclusive"] / cases
    return {"source": str(run.resolve()), "totals": dict(totals), "records": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = [replay(run) for run in args.run]
    payload = {"kind": "offline decision replay; unchanged captured ranking and observations",
               "new_lifecycles": 0, "new_llm_calls": 0, "synthesized_observations": 0,
               "limitation": "No new planner actions executed; missing captured observations remain unknown.",
               "rates_denominator": "all captured cases per mode; raw Top1 uses all fault cases",
               "runs": runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{ "source": r["source"], "totals": r["totals"]} for r in runs]))


if __name__ == "__main__":
    main()
