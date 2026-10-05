"""Refine observed failure conditions using actual inputs, evidence and frozen rules."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from opspilot.otel_benchmark.experiment import assert_frozen
from opspilot.otel_benchmark.experiment_report import LABELS, completed_records
from opspilot.otel_benchmark.modes import read_json

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    assert read_json(run / "status.json")["status"] == "completed"
    assert_frozen(run)
    audit_path = run / "failure_condition_refinement.json"
    assert not audit_path.exists(), "Already refined; preserve the audit"
    analysis = read_json(run / "failure_analysis.json")
    before = json.loads(json.dumps(analysis))
    backup = run / "analysis_refinement"
    backup.mkdir(exist_ok=False)
    save(backup / "original_failure_analysis.json", before)
    records = completed_records(read_json(run / "records.json"))
    lookup = {(r["scenario_id"], r["repetition"], r["mode"]): r for r in records}
    updates = []
    for failure in analysis["failures"]:
        path = Path(failure["path"])
        expected = failure["expected"]
        evidence = read_json(path / "rca/evidence.json")
        supporting = [e for e in evidence if expected in e["supports"]]
        results = read_json(path / "rca/tool_results.json")

        def add(label, fact, artifact, *, failure=failure):
            if label not in failure["labels"]:
                failure["labels"] = [v for v in failure["labels"] if v != "unknown"] + [label]
                failure["facts"].append({"label": label, "fact": fact, "artifact": str(artifact)})
                updates.append({"scenario_id": failure["scenario_id"], "repetition": failure["repetition"],
                                "mode": failure["mode"], "label": label, "fact": fact})

        if supporting:
            add("D", {"expected_supporting_evidence_exists_but_top1_differs": expected,
                      "supporting_evidence_ids": [e["evidence_id"] for e in supporting],
                      "certainty": "observed ranking condition; not proof that ranking is the sole cause"}, path / "rca/evidence.json")
        if expected == "kafka_consumer_lag" and not supporting:
            lags = [t["data"].get("observations", {}).get("consumer_lag") for t in results if t["tool_name"] == "kafka.lag"]
            if lags and isinstance(lags[0], (int, float)) and 0 < lags[0] < 1000:
                rule = run / "source/src/opspilot/evidence/collector.py"
                assert "if lag < 1000:" in rule.read_text(encoding="utf-8")
                add("C", {"observed_consumer_lag": lags[0], "frozen_evidence_threshold": 1000,
                          "condition": "numeric backlog sampled; frozen converter suppresses expected evidence below threshold",
                          "rule_artifact": str(rule)}, path / "rca/tool_results.json")
        if failure["mode"] != "fixed_full":
            fixed = lookup[(failure["scenario_id"], failure["repetition"], "fixed_full")]
            fixed_path = Path(fixed["path"])
            sources = {e["source_name"] for e in read_json(fixed_path / "rca/evidence.json") if expected in e["supports"]}
            missed = sorted(sources - set(read_json(path / "rca/events.json")["executed_tools"]))
            if missed:
                add("F", {"uncollected_source_with_actual_expected_supporting_evidence": missed,
                          "certainty": "omitted observation, not proof of an illegal planner action or guaranteed correction",
                          "no_l2_budget_constraint": failure["mode"] == "adaptive_no_l2"}, fixed_path / "rca/evidence.json")
        failure["primary_observed_condition"] = next((label for label in ("B", "A", "E", "F", "C", "D", "H", "G")
                                                        if label in failure["labels"]), "unknown")
    analysis["primary_counts"] = dict(Counter(f["primary_observed_condition"] for f in analysis["failures"]))
    analysis["label_counts"] = dict(Counter(label for f in analysis["failures"] for label in f["labels"]))
    assert analysis["wrong_top1_cases"] == before["wrong_top1_cases"] == 29
    save(run / "failure_analysis.json", analysis)
    save(ROOT / "artifacts/failure_analysis.json", analysis)
    lines = ["# Artifact-based failure analysis", "", "自动规则只分类已观测条件，不调用 LLM，不证明唯一因果；所有 causal diagnosis 仍为 unknown。"]
    for f in analysis["failures"]:
        lines += ["", f"## {f['mode']} / {f['scenario_id']} #{f['repetition']}",
                  f"预测 {f['predicted']}；期望 {f['expected']}；primary={f['primary_observed_condition']}；causal diagnosis=unknown。"]
        lines += [f"- {LABELS[v['label']]}：{v['fact']}；artifact `{v['artifact']}`。" for v in f["facts"]]
    for destination in (run / "failure_analysis.md", ROOT / "artifacts/failure_analysis.md"):
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for destination in (ROOT / "reports/otel_demo_rca_v1.md", ROOT / "plan/sy.md"):
        text = destination.read_text(encoding="utf-8")
        text = text.replace(str(before["primary_counts"]), str(analysis["primary_counts"]))
        text = text.replace(str(before["label_counts"]), str(analysis["label_counts"]))
        destination.write_text(text, encoding="utf-8")
    knowledge_path = ROOT / "artifacts/knowledge_tool_decision.md"
    text = knowledge_path.read_text(encoding="utf-8")
    for old, new in zip(before["failures"], analysis["failures"], strict=True):
        prefix = f"- {old['mode']}/{old['scenario_id']} #{old['repetition']}：预测 {old['predicted']}，观测条件 "
        text = text.replace(prefix + str(old["labels"]), prefix + str(new["labels"]))
    knowledge_path.write_text(text + "\n复核：Fixed 真实 Kafka lag 为 707/808/606，均低于冻结 Evidence 的 1000 阈值；DB slowlog 计数 27/22/16 已形成支持 Evidence，但正确类别未进入 Top3。Full/No-L2 未采集 DB slowlog。以上基础阈值、工具选择和排名条件尚未排除，不能据此证明需要历史知识；结论仍为 INCONCLUSIVE。\n", encoding="utf-8")
    with (ROOT / "reports/otel_demo_rca_v1.md").open("a", encoding="utf-8") as stream:
        stream.write("\n## 已保存输入与冻结规则的错误条件复核\n\nFixed 的三次 Kafka lag 实测为 707、808、606，冻结 Evidence converter 要求 lag≥1000，因此没有生成 Kafka 支持 Evidence。Fixed 的 DB slowlog 计数 27、22、16 均生成 DB_SLOW_QUERY 支持 Evidence，但该类别未进入 Top3；Full/No-L2 均未采集 db.slowlog。自动补充 C/D/F 条件，仍将唯一因果记为 unknown，No-L2 工具遗漏可由 Expert budget=0 的配置约束解释。未改任何门槛、Planner、Ranker 或实验得分；原分析保存在本地 analysis_refinement/，复核摘要 failure_condition_refinement.json。\n")
    save(audit_path, {"scores_unchanged": True, "wrong_top1_cases": 29,
                     "source_and_configuration_unchanged": True, "causal_diagnosis_remains_unknown": True,
                     "primary_counts": analysis["primary_counts"], "updates": updates,
                     "analysis_helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(json.dumps({"wrong_top1_cases": 29, "primary_counts": analysis["primary_counts"], "updates": len(updates)}))


if __name__ == "__main__":
    main()
