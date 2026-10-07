"""Audit a fresh closure run and report its actual results without changing scores."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from opspilot.otel_benchmark.experiment import assert_frozen
from opspilot.otel_benchmark.experiment_report import completed_records, metrics
from opspilot.otel_benchmark.modes import MODES, read_json
from opspilot.otel_benchmark.runner import ROOT, command, containers, save
from opspilot.otel_benchmark.scoring import score_report

BEFORE = ROOT / "artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16"


def execute(arguments, log, *, env=None):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, check=False,
                            encoding="utf-8", errors="replace", env=env)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Validation failed; see {log}")
    return result.stdout + result.stderr


def rate(value):
    return f"{value['numerator']}/{value['denominator']}"


def finalize(run):
    assert_frozen(run)
    assert read_json(run / "status.json")["status"] == "completed"
    records = completed_records(read_json(run / "records.json"))
    assert len(records) == 63
    keys = {(r["scenario_id"], r["repetition"], r["mode"]) for r in records}
    assert len(keys) == 63
    dataset = read_json(run / "dataset.json")
    assert keys == {(s["scenario_id"], repeat, mode) for s in dataset["scenarios"]
                    for repeat in range(1, 4) for mode in MODES}
    pairs = [p for p in read_json(run / "pairs.json") if p["status"] == "completed"]
    assert len(pairs) == 21 and sum(p.get("fault_observed") is True for p in pairs) == 18
    assert all(p["recovery_health"] and p["recovery_queries_success"]
               and p.get("symptom_recovery_confirmed") is not False for p in pairs)
    for record in records:
        directory = Path(record["path"])
        actual = score_report(read_json(directory / "rca/report.json"), record["score"]["expected"])
        assert actual == record["score"] == read_json(directory / "benchmark/score.json")
    execute([sys.executable, "scripts/otel_demo/audit_rca_release.py", str(run)], run / "independent_audit.log")
    live = containers()
    assert len(live) == 28 and all(c["state"] == "running" and c["health"] not in ("starting", "unhealthy") for c in live)
    assert not command("git", "-C", str(ROOT / ".external/opentelemetry-demo"), "status", "--short")
    # Check semantic scoring/ground truth identity against the old frozen release.
    unchanged = ["src/opspilot/otel_benchmark/scoring.py", "benchmarks/datasets/otel_demo/v1/scenarios.yaml"]
    old_manifest = read_json(BEFORE / "source_manifest.json")
    assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == old_manifest[name] for name in unchanged)
    test_env = {k: v for k, v in os.environ.items() if not k.startswith("OPSPILOT_")}
    test_env["OPSPILOT_LLM_ENABLED"] = "false"
    tests = execute([sys.executable, "-m", "pytest", "-q"], run / "pytest_final.log", env=test_env)
    ruff = execute([sys.executable, "-m", "ruff", "check", "src/opspilot", "tests", "scripts/otel_demo"], run / "ruff_final.log")
    values = metrics(records)
    save(run, "metrics.json", values)
    full, fixed = (values[m]["overall"] for m in MODES[:2])
    reduction = 1 - full["avg_tool_calls"] / fixed["avg_tool_calls"]
    target = full["top1"]["numerator"] >= 17 and full["no_fault_accuracy"]["numerator"] >= 2 and reduction >= 0.2
    target_status = "ACCURACY_TARGET_MET" if target else "ACCURACY_TARGET_NOT_MET"
    failures = []
    for record in records:
        if record["score"]["top1"] or record["score"]["normal_correct"] is True:
            continue
        directory = Path(record["path"])
        evidence = read_json(directory / "rca/evidence.json")
        results = read_json(directory / "rca/tool_results.json")
        expected = record["score"]["expected"]
        supporting = [e for e in evidence if expected in e["supports"]]
        failures.append({"scenario": record["scenario_id"], "repeat": record["repetition"], "mode": record["mode"],
                         "expected": expected, "predicted": record["score"]["predicted"],
                         "supporting_evidence": supporting, "tools": [r["tool_name"] for r in results],
                         "condition": "fault evidence causes a Normal false positive" if expected == "no_fault"
                                      else "ranking despite supporting evidence" if supporting else "no supporting evidence in gathered observations",
                         "path": str(directory.relative_to(ROOT)),
                         "note": "Observed condition; not a claim of unique causal attribution"})
    save(run, "failure_analysis.json", failures)
    old = read_json(BEFORE / "metrics.json")["full_adaptive"]
    verification = {"status": "passed", "target_status": target_status, "completed_at": datetime.now(UTC).isoformat(),
                    "fresh_paired_lifecycles": len(pairs), "observed_recovered_fault_lifecycles": 18,
                    "actual_diagnoses": len(records), "independent_audit": "independent_audit.json",
                    "ground_truth_and_scorer_unchanged": unchanged,
                    "pytest_passed": int(re.search(r"(\d+) passed", tests)[1]),
                    "pytest_skipped": int(re.search(r"(\d+) skipped", tests)[1]), "ruff": ruff.strip(),
                    "tool_reduction": reduction, "old_full_top1": old["overall"]["top1"],
                    "new_full_top1": full["top1"], "best_actual_full_result": full["top1"],
                    "running_services": 28, "upstream_clean": True}
    save(run, "final_verification.json", verification)
    relative = run.relative_to(ROOT).as_posix()
    table = ["| Mode | Fault Top1 | Fault Top3 | Normal | Avg tools | P50 / P95 ms |",
             "|---|---:|---:|---:|---:|---:|"]
    for mode in MODES:
        v = values[mode]["overall"]
        table.append(f"| {mode} | {rate(v['top1'])} | {rate(v['top3'])} | {rate(v['no_fault_accuracy'])} | {v['avg_tool_calls']:.2f} | {v['p50_latency_ms']:.0f} / {v['p95_latency_ms']:.0f} |")
    scenarios = ["| Scenario | Before Full Top1 | Fresh Full Top1 | Fresh Fixed Top1 |",
                 "|---|---:|---:|---:|"]
    for scenario in values["full_adaptive"]["per_scenario"]:
        field = "no_fault_accuracy" if scenario == "normal" else "top1"
        scenarios.append(f"| {scenario} | {rate(old['per_scenario'][scenario][field])} | {rate(values['full_adaptive']['per_scenario'][scenario][field])} | {rate(values['fixed_full']['per_scenario'][scenario][field])} |")
    text = ["# RCA Accuracy Closure", "", f"**{target_status}**", "",
            f"Fresh run: `{relative}`. 21 completed baseline/fault/reset/recovery lifecycles, 18 observed and recovered faults, 63 actual investigations. Same ground truth, scoring, five-user official Locust load, budgets and model configuration as the earlier formal release.", "",
            *table, "", *scenarios, "",
            f"Full tool calls decrease by {reduction:.2%} relative to Fixed. P50/P95 measure individual RCA execution, excluding lifecycle warmup/stabilization/recovery. Model cost is not estimated; actual request/response/usage and fallback records are retained.", "",
            "## General mechanisms and attribution limits", "",
            "1. Preserve temporal observations; compare robust historical and recent windows, retaining unknown when baseline samples are absent. Queue growth below the old absolute threshold can now become evidence; stable low cache hit rates are suppressed.",
            "2. Require material sustained resource growth and directional deterioration. Falling latency and small resource variation cannot vote for a new fault.",
            "3. Distinguish typed database query latency from long successful spans. Long spans alone remain diagnostic context; protocol timeout status and baseline latency degradation are separate evidence.",
            "4. Weight specific measurements above propagated symptoms, split ambiguous hypotheses and cap repeated observations per tool/source. Additional distinct tools provide bounded corroboration; distinct tools are not necessarily independent physical backends, so this is not a calibrated causal probability.",
            "5. Retain distinct evidence types in the bounded Planner context. Messaging producer/consumer operations are routing hints without root-cause support. No domain tool is added to the seed or executed unconditionally.", "",
            "The three development replays and their reasons are in [accuracy_closure_development.md](accuracy_closure_development.md). Replay results cannot establish fresh accuracy or repair missing tool selection. Before/after experiments support the combined change; there is no fresh per-mechanism ablation establishing unique causality.", "",
            "## Remaining observed failures", ""]
    if failures:
        for failure in failures:
            text.append(f"- {failure['mode']} / {failure['scenario']} repeat {failure['repeat']}: expected `{failure['expected']}`, predicted `{failure['predicted']}`; {failure['condition']}. Tools: {', '.join(failure['tools'])}.")
    else:
        text.append("No Top1 failures in this run. This remains a small controlled demo experiment, not a production accuracy claim.")
    text += ["", "## Integrity and reproduction", "",
             "No scenario IDs or injection flag names in diagnosis rules. Ground truth/control metadata is guarded at tool and LLM boundaries and scored only after diagnosis. Benchmark scorer and dataset bytes match the old frozen run. Every mode shares the same alert window, with rotating mode order. No Adaptive all-tool invocation. Frozen source/config hashes, actual calls, secret absence, all faults off and 28 running services pass independent audit.", "",
             f"Validation: {verification['pytest_passed']} passed, {verification['pytest_skipped']} skipped; Ruff passed. Raw telemetry is retained locally; compact configurations, scores, metrics, failure conditions and verification are versioned.", "",
             "```powershell", ". scripts/otel_demo/local_live_env.ps1",
             "python -m opspilot.otel_benchmark.experiment --config benchmarks/datasets/otel_demo/v1/accuracy_closure.yaml",
             "python scripts/otel_demo/finalize_accuracy_closure.py artifacts/otel_demo_rca_v1/<new-run>", "```", "",
             f"[Actual records](../{relative}/records.json) · [Frozen configuration](../{relative}/config.json) · [Independent audit](../{relative}/independent_audit.json) · [Final verification](../{relative}/final_verification.json)"]
    (ROOT / "reports/accuracy_closure.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    completion_date = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    marker = f"## RCA Accuracy Closure ({completion_date})"
    readme = ROOT / "README.md"
    original = readme.read_text(encoding="utf-8")
    assert marker not in original, "Preserve existing finalization; avoid duplicate reports"
    readme.write_text(original + f"\n{marker}\n\n**{target_status}** — fresh Full Fault Top1 {rate(full['top1'])}, Top3 {rate(full['top3'])}, Normal {rate(full['no_fault_accuracy'])}; {reduction:.2%} fewer tool calls than Fixed.\n\n[Formal report](reports/accuracy_closure.md) · [Development record](reports/accuracy_closure_development.md)\n", encoding="utf-8")
    with (ROOT / "plan/task2.md").open("a", encoding="utf-8") as stream:
        stream.write(f"\n## Completion record ({completion_date})\n\n**{target_status}**. Completed three general rule iterations, fresh release and independent verification. Full Fault Top1 {rate(full['top1'])}; Top3 {rate(full['top3'])}; Normal {rate(full['no_fault_accuracy'])}; tools {full['avg_tool_calls']:.2f} versus Fixed {fixed['avg_tool_calls']:.2f}. See `reports/accuracy_closure.md` and `{relative}/final_verification.json`. Failed targets are reported honestly; no additional rule tuning after freeze.\n")
    print(json.dumps(verification), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    if args.wait:
        while read_json(run / "status.json")["status"] == "running":
            time.sleep(30)
        while (ROOT / ".external/otel-benchmark.lock").exists():
            time.sleep(5)
    finalize(run)


if __name__ == "__main__":
    main()
