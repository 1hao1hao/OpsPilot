"""Record measured failure conditions after a fresh run, without reranking it."""

import argparse
from datetime import datetime
from pathlib import Path

from opspilot.otel_benchmark.experiment_report import completed_records
from opspilot.otel_benchmark.modes import read_json
from opspilot.otel_benchmark.runner import ROOT, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    assert read_json(run / "final_verification.json")["status"] == "passed"
    conditions = []
    for record in completed_records(read_json(run / "records.json")):
        score = record["score"]
        if score["top1"] or score["normal_correct"] is True:
            continue
        directory = Path(record["path"])
        event = read_json(directory / "alert.json")
        timestamp = datetime.fromisoformat(event["timestamp"]).timestamp()
        results = read_json(directory / "rca/tool_results.json")
        tools = [r["tool_name"] for r in results]
        item = {"scenario": record["scenario_id"], "repeat": record["repetition"], "mode": record["mode"],
                "expected": score["expected"], "predicted": score["predicted"], "conditions": [],
                "note": "Measured conditions only; no counterfactual reranking, new formal scores or rule iteration"}
        if score["expected"] == "kafka_consumer_lag" and "kafka.lag" not in tools:
            item["conditions"].append("No-L2 deliberately excludes the domain lag measurement" if record["mode"] == "adaptive_no_l2"
                                      else "Planner did not collect the lag measurement within the frozen budget")
        for result in results:
            if result["tool_name"] == "traces.query" and result.get("data"):
                spans = [s for t in result["data"]["observations"].get("traces", []) for s in t["spans"]]
                database = [s for s in spans if s.get("db_system") in {"postgresql", "postgres", "mysql", "mssql", "oracle"}]
                slow = [s for s in database if s["duration_ms"] > 1000]
                errors = [s for s in spans if s["status"] in {"ERROR", "TIMEOUT"}]
                item["trace_measurements"] = {"database_spans": len(database), "slow_database_spans": len(slow),
                                              "error_or_timeout_spans": len(errors),
                                              "slow_database_samples": [{"duration_ms": s["duration_ms"],
                                                                         "seconds_relative_to_alert": s.get("start_time", timestamp) - timestamp}
                                                                        for s in slow]}
                if slow and score["predicted"][0] == "db_slow_query" and score["expected"] != "db_slow_query":
                    item["conditions"].append("Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority")
        conditions.append(item)
    save(run, "failure_conditions.json", conditions)
    report = ROOT / "reports/accuracy_closure.md"
    marker = "## Measured residual failure conditions"
    assert marker not in report.read_text(encoding="utf-8")
    lines = ["", marker, "", "These measurements explain limitations of the frozen rules; they do not modify the recorded outcomes.", ""]
    for item in conditions:
        descriptions = "; ".join(item["conditions"]) or "Supporting facts and ranking remain in failure_analysis.json; unique causal attribution is unresolved"
        measurement = item.get("trace_measurements", {})
        detail = f" DB slow spans {measurement.get('slow_database_spans')}/{measurement.get('database_spans')}; error/timeout spans {measurement.get('error_or_timeout_spans')}." if measurement else ""
        lines.append(f"- {item['mode']} / {item['scenario']} repeat {item['repeat']}: {descriptions}.{detail}")
    lines += ["", f"[Measured counts and time offsets](../{run.relative_to(ROOT).as_posix()}/failure_conditions.json). No additional rule iteration is performed after the three permitted development iterations."]
    with report.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    print(f"Recorded measured conditions for {len(conditions)} failed diagnoses")


if __name__ == "__main__":
    main()
