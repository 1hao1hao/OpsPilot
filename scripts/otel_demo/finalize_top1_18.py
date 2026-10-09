"""Validate the new 18/18 target without overwriting previous release reports."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from opspilot.otel_benchmark.experiment import assert_frozen
from opspilot.otel_benchmark.experiment_report import completed_records, metrics
from opspilot.otel_benchmark.modes import MODES, read_json
from opspilot.otel_benchmark.runner import ROOT, command, containers, save
from opspilot.otel_benchmark.scoring import score_report

BEFORE = ROOT / "artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e"


def execute(arguments, output, *, env=None):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, check=False,
                            encoding="utf-8", errors="replace", env=env)
    output.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Verification failed; see {output}")
    return result.stdout + result.stderr


def finalize(run, report_name="top1_18"):
    assert read_json(run / "status.json")["status"] == "completed"
    assert_frozen(run)
    records = completed_records(read_json(run / "records.json"))
    dataset = read_json(run / "dataset.json")
    keys = {(s["scenario_id"], repetition, mode) for s in dataset["scenarios"]
            for repetition in range(1, 4) for mode in MODES}
    assert len(records) == 63 and {(r["scenario_id"], r["repetition"], r["mode"]) for r in records} == keys
    pairs = read_json(run / "pairs.json")
    completed = [p for p in pairs if p["status"] == "completed"]
    assert len(completed) == 21 and sum(p.get("fault_observed") is True for p in completed) == 18
    assert all(p["recovery_health"] and p["recovery_queries_success"]
               and p.get("symptom_recovery_confirmed") is not False for p in completed)
    for r in records:
        directory = Path(r["path"])
        assert score_report(read_json(directory / "rca/report.json"), r["score"]["expected"]) == r["score"]
        assert read_json(directory / "benchmark/score.json") == r["score"]
    unchanged = ["src/opspilot/otel_benchmark/scoring.py", "benchmarks/datasets/otel_demo/v1/scenarios.yaml"]
    old_manifest = read_json(BEFORE / "source_manifest.json")
    assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == old_manifest[name] for name in unchanged)
    execute([sys.executable, "scripts/otel_demo/audit_rca_release.py", str(run)], run / "independent_audit.log")
    live = containers()
    assert len(live) == 28 and all(c["state"] == "running" and c["health"] not in ("starting", "unhealthy") for c in live)
    assert not command("git", "-C", str(ROOT / ".external/opentelemetry-demo"), "status", "--short")
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPSPILOT_")}
    env["OPSPILOT_LLM_ENABLED"] = "false"
    tests = execute([sys.executable, "-m", "pytest", "-q"], run / "pytest_final.log", env=env)
    execute([sys.executable, "-m", "ruff", "check", "src/opspilot", "tests", "scripts/otel_demo"], run / "ruff_final.log")
    values = metrics(records)
    save(run, "metrics.json", values)
    full, fixed = (values[m]["overall"] for m in MODES[:2])
    reduction = 1 - full["avg_tool_calls"] / fixed["avg_tool_calls"]
    target = (full["top1"]["numerator"] == full["top1"]["denominator"] == 18
              and full["no_fault_accuracy"]["numerator"] >= 2 and reduction >= 0.2)
    status = "ACCURACY_TARGET_MET" if target else "ACCURACY_TARGET_NOT_MET"
    failures = [r for r in records if not r["score"]["top1"] and r["score"]["normal_correct"] is not True]
    save(run, "failure_analysis.json", failures)
    verification = {"status": "passed", "target_status": status, "completed_at": datetime.now(UTC).isoformat(),
                    "fresh_paired_lifecycles": 21, "observed_recovered_fault_lifecycles": 18, "actual_diagnoses": 63,
                    "new_full_top1": full["top1"], "normal": full["no_fault_accuracy"], "tool_reduction": reduction,
                    "ground_truth_and_scorer_unchanged": unchanged,
                    "pytest_passed": int(re.search(r"(\d+) passed", tests)[1]),
                    "pytest_skipped": int(re.search(r"(\d+) skipped", tests)[1]), "ruff": "passed"}
    verification["llm"] = {name: sum(r["llm"][name] for r in records)
                           for name in ("api_calls", "api_successes", "api_failures", "planner_fallbacks")}
    save(run, "final_verification.json", verification)
    relative = run.relative_to(ROOT).as_posix()
    manifest = read_json(run / "source_manifest.json")
    config_file = next(name for name in manifest if name.endswith(".yaml") and name not in unchanged)
    planner_changed = manifest["src/opspilot/investigation/planner.py"] != old_manifest["src/opspilot/investigation/planner.py"]
    table = ["| Mode | Fault Top1 | Fault Top3 | Normal | Avg tools | P50 / P95 ms |",
             "|---|---:|---:|---:|---:|---:|"]
    for mode in MODES:
        v = values[mode]["overall"]
        rates = [f"{v[k]['numerator']}/{v[k]['denominator']}" for k in ("top1", "top3", "no_fault_accuracy")]
        table.append(f"| {mode} | {' | '.join(rates)} | {v['avg_tool_calls']:.2f} | {v['p50_latency_ms']:.0f} / {v['p95_latency_ms']:.0f} |")
    scenarios = ["| Scenario | Full Top1 | Fixed Top1 | No-L2 Top1 |", "|---|---:|---:|---:|"]
    for scenario in values["full_adaptive"]["per_scenario"]:
        field = "no_fault_accuracy" if scenario == "normal" else "top1"
        rates = [values[m]["per_scenario"][scenario][field] for m in MODES]
        scenarios.append(f"| {scenario} | " + " | ".join(f"{v['numerator']}/{v['denominator']}" for v in rates) + " |")
    report = ["# Incident-local evidence: 18/18 target", "", f"**{status}**", "", *table, "",
              f"Fresh run `{relative}`: 21 recovered lifecycles, 18 observed faults, 63 actual diagnoses. Full uses {reduction:.2%} fewer tools than Fixed. Rules and configuration were frozen before the run.", "",
              "Previous fresh release: Full Fault Top1 16/18, Normal 2/3. Its report remains in accuracy_closure.md. Development-only replay of those observations reaches 18/18 and Normal 3/3; it is not the result in the table above.", "",
              "The two previous fault failures were caused by isolated slow database calls. One was 181 seconds before the alert. The Normal timeout false positive asserted timeout despite an observed zero timeout rate.", "",
              "Database spans now need incident-local corroboration from like calls: at least three slow calls and a group mean exceeding the existing one-second latency budget. Older or isolated spans remain diagnostic hints without causal votes. Database domain evidence uses the same corroboration. Timestamp-less legacy snapshots retain their previous behavior; absence of history is not treated as proof of a healthy baseline. This cohort rule does not establish causal certainty and may miss rare-tail incidents.", "",
              "An observed zero timeout rate blocks a timeout inference from latency growth. Actual protocol timeouts still contribute. Gate, Ranker, Fallback, scorer, labels, fault controls, load, budgets and rotating mode order remain unchanged. No scenario-specific diagnosis rules or ground truth at the Engine boundary.", "",
              *( ["Resource baseline comparisons discard samples before a large downward discontinuity, require at least three new reference samples and retain the material delta/ratio/noise thresholds. The observed drop is not asserted to prove a restart. Normal sampled RPC outcomes remain visible as context without causal votes. The minimal Planner prompt and capability descriptions ask for observed dependencies before expert guesses and a different observed mechanism after a normal domain measurement; the real model still selects actions, subject to unchanged authorization and budgets.", ""] if planner_changed else ["Planner remained unchanged for this iteration.", ""] ),
              *scenarios, "",
              f"Actual LLM calls: {verification['llm']}. Cached development probes and the first fresh release remain in [the development record](top1_18_development.md); none are mixed into this run's accuracy.", "",
              f"Validation: {verification['pytest_passed']} passed, {verification['pytest_skipped']} skipped; Ruff and independent audit passed. All faults off; 28 services healthy/running. Actual API requests, responses and usage retained. Raw telemetry remains local; this is a small controlled demo benchmark, not a production accuracy guarantee.", "",
              "## Remaining observed errors", ""]
    report.extend(f"- {r['mode']} / {r['scenario_id']} repeat {r['repetition']}: expected {r['score']['expected']}, predicted {r['score']['predicted']}." for r in failures)
    if not failures:
        report.append("No Top1 or Normal errors in this run.")
    report += ["", f"[Metrics](../{relative}/metrics.json) | [Verification](../{relative}/final_verification.json) | [Independent audit](../{relative}/independent_audit.json)", "",
               "```powershell", ". scripts/otel_demo/local_live_env.ps1",
               f"python -m opspilot.otel_benchmark.experiment --config {config_file}",
               "python scripts/otel_demo/finalize_top1_18.py artifacts/otel_demo_rca_v1/<new-run>", "```"]
    (ROOT / f"reports/{report_name}.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    marker = f"## Incident-local RCA validation ({run.name})"
    readme = ROOT / "README.md"
    original = readme.read_text(encoding="utf-8")
    assert marker not in original
    readme.write_text(original + f"\n{marker}\n\n**{status}**: fresh Full Fault Top1 {full['top1']['numerator']}/18, Normal {full['no_fault_accuracy']['numerator']}/3, {reduction:.2%} fewer tools than Fixed. [Formal report](reports/{report_name}.md).\n", encoding="utf-8")
    print(json.dumps(verification), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--report-name", default="top1_18")
    args = parser.parse_args()
    finalize(args.run.resolve(), args.report_name)
