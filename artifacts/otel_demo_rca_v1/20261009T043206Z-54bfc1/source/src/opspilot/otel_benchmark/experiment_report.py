"""Compute release metrics, evidence-linked failure analysis and a conservative knowledge decision."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from .modes import MODES, read_json
from .runner import ROOT, save
from .scoring import summarize

LABELS = {"A": "Telemetry 缺失", "B": "Tool adapter 错误", "C": "Evidence 未提取",
          "D": "Ranking 错误", "E": "Gate 接受错误 Top1", "F": "Planner 未采集区分性工具",
          "G": "Budget 耗尽", "H": "LLM 调用/决策失败并 fallback", "I": "Taxonomy gap", "unknown": "无法确定"}


def aggregate(records):
    summary = summarize(records)["overall"]
    scores = [r["score"] for r in records if r.get("score")]
    latencies = [s["latency_ms"] for s in scores]
    llms = [r["llm"] for r in records if r.get("score")]
    def average(field):
        return sum(s[field] for s in scores) / len(scores) if scores else None
    summary.update(avg_tool_calls=average("tool_calls"), avg_expert_calls=average("expert_calls"),
                   avg_investigation_rounds=average("investigation_rounds"),
                   avg_llm_calls=sum(s["api_calls"] for s in llms) / len(llms) if llms else None,
                   p50_latency_ms=float(np.percentile(latencies, 50)) if latencies else None,
                   p95_latency_ms=float(np.percentile(latencies, 95)) if latencies else None,
                   llm_used_runs=sum(s["llm_used"] for s in llms), fallback_runs=sum(s["fallback_occurred"] for s in llms),
                   api_calls=sum(s["api_calls"] for s in llms), api_successes=sum(s["api_successes"] for s in llms),
                   api_failures=sum(s["api_failures"] for s in llms), planner_successes=sum(s["planner_successes"] for s in llms),
                   planner_fallbacks=sum(s["planner_fallbacks"] for s in llms),
                   tokens={k: sum(s["tokens"][k] for s in llms) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
                   usage_available_calls=sum(s["usage_available_calls"] for s in llms), estimated_model_cost=None)
    return summary


def metrics(records):
    return {mode: {"overall": aggregate([r for r in records if r["mode"] == mode]),
                   "per_scenario": {scenario: aggregate([r for r in records if r["mode"] == mode and r["scenario_id"] == scenario])
                                    for scenario in sorted({r["scenario_id"] for r in records})}}
            for mode in MODES}


def completed_records(records):
    selected = {}
    for record in records:
        if (record["status"] == "completed" and record.get("recovery_health")
                and record.get("recovery_queries_success") and record.get("symptom_recovery_confirmed") is not False):
            selected[(record["scenario_id"], record["repetition"], record["mode"])] = record
    return list(selected.values())


def analyze_failures(records):
    """Labels describe observed failure conditions, not unproved counterfactual causality."""
    failures = []
    lookup = {(r["scenario_id"], r["repetition"], r["mode"]): r for r in records}
    for record in records:
        if record["score"]["top1"]:
            continue
        directory = Path(record["path"])
        expected = record["score"]["expected"]
        evidence = read_json(directory / "rca/evidence.json")
        results = read_json(directory / "rca/tool_results.json")
        events = read_json(directory / "rca/events.json")
        labels, facts = [], []

        def add(label, fact, source, *, labels=labels, facts=facts):
            labels.append(label)
            facts.append({"label": label, "fact": fact, "artifact": str(source)})

        failed_tools = [r["tool_name"] for r in results if r["status"] != "success"]
        if failed_tools:
            add("B", {"failed_tools": failed_tools}, directory / "rca/tool_results.json")
        if record.get("fault_observed") is False:
            add("A", "Controller did not confirm the intended observable fault effect", Path(record["lifecycle_path"]) / "benchmark/observed_delta.json")
        if record["score"]["gate_pass"]:
            add("E", {"accepted_top1": record["score"]["predicted"][0], "expected": expected}, directory / "rca/events.json")
        supports = [e for e in evidence if expected in e["supports"]]
        if supports and expected in record["score"]["predicted"]:
            add("D", {"expected_is_lower_rank": expected, "supporting_evidence_ids": [e["evidence_id"] for e in supports]}, directory / "rca/evidence.json")
        fixed = lookup.get((record["scenario_id"], record["repetition"], "fixed_full"))
        if fixed and record["mode"] != "fixed_full":
            fixed_path = Path(fixed["path"])
            fixed_evidence = read_json(fixed_path / "rca/evidence.json")
            expected_tools = {e["source_name"] for e in fixed_evidence if expected in e["supports"]}
            missed = sorted(expected_tools - set(events["executed_tools"]))
            if fixed["score"]["top1"] and missed:
                add("F", {"uncollected_tools_supporting_fixed_correct_answer": missed}, fixed_path / "rca/evidence.json")
            # Same incident window: if the expected source was executed but only the
            # fixed results carry it, record a data/evidence discrepancy, not an invented bug.
            if not supports and expected_tools and expected_tools <= set(events["executed_tools"]):
                add("C", {"expected_evidence_absent_despite_executed_sources": sorted(expected_tools),
                          "certainty": "discrepancy; investigate query sampling versus extraction"}, directory / "rca/evidence.json")
        if record["score"]["budget_exhausted"]:
            add("G", "The final gate decision explicitly records exhausted budget", directory / "rca/events.json")
        if record["llm"]["fallback_occurred"]:
            add("H", {"api_failures": record["llm"]["api_failures"], "planner_fallbacks": record["llm"]["planner_fallbacks"]}, directory / "llm/planner_decisions.json")
        scenario = read_json(Path(record["lifecycle_path"]) / "scenario.json")
        taxonomy_note = scenario["ground_truth"].get("taxonomy_note")
        if taxonomy_note:
            add("I", {"mapping_note": taxonomy_note, "causal_role": "unproven"}, Path(record["lifecycle_path"]) / "scenario.json")
        primary = next((label for label in ("B", "A", "E", "F", "C", "D", "H", "G") if label in labels), "unknown")
        failures.append({"scenario_id": record["scenario_id"], "repetition": record["repetition"], "mode": record["mode"],
                         "primary_observed_condition": primary, "labels": labels or ["unknown"], "facts": facts,
                         "causal_diagnosis": "unknown; labels are artifact-backed conditions, not proof of a sole cause",
                         "predicted": record["score"]["predicted"], "expected": expected,
                         "evidence": [{"evidence_type": e["evidence_type"], "fact": e["fact"], "supports": e["supports"]} for e in evidence],
                         "path": str(directory)})
    return {"wrong_top1_cases": len(failures), "primary_counts": dict(Counter(f["primary_observed_condition"] for f in failures)),
            "label_counts": dict(Counter(label for f in failures for label in f["labels"])), "failures": failures}


def knowledge_decision(failures):
    # No verified incident/runbook corpus was supplied. Neither benchmark labels nor
    # flag source code are permitted to serve as historical knowledge for the Agent.
    return {"decision": "INCONCLUSIVE", "reason": "尚无经核验的历史 incident/runbook 证明增量区分信息；本轮还存在工具选择、排名、Gate、预算、采样/窗口或 taxonomy 限制，不能从错例直接推断需要 RAG。",
            "conditions": {"multiple_real_failures": failures["wrong_top1_cases"] > 1,
                           "telemetry_complete_for_every_failure": "inspect per-case telemetry artifacts; not presumed",
                           "live_evidence_proven_unable_to_disambiguate": "unverified",
                           "verified_external_discriminating_knowledge": False,
                           "simple_pipeline_bugs_ruled_out": False},
            "implementation": "No EvalRAG or Knowledge Tool integration",
            "external_knowledge_candidates": ["DB 锁等待 runbook 与独立历史 incident 的锁/SQL 区分模式", "consumer group/service dependency 文档"],
            "verification_plan": "先修复并冻结 telemetry/window/tool selection/ranking 问题，再核验独立脱敏知识文档；若五项标准满足，单独做 Without/With Knowledge，比较 Top1/Top3、Tool Cost、Latency，知识不能直接决定 RootCause。"}


def rate(value):
    return f"{value['numerator']}/{value['denominator']}" if value["denominator"] else "N/A"


def number(value):
    return f"{value:.2f}" if value is not None else "N/A"


def generate(output, report_path=None):
    raw_records = read_json(output / "records.json")
    records = completed_records(raw_records)
    dataset = read_json(output / "dataset.json")
    config = read_json(output / "config.json")
    environment = read_json(output / "environment.json")
    status = read_json(output / "status.json")
    calculated = metrics(records)
    failures = analyze_failures(records)
    knowledge = knowledge_decision(failures)
    save(output, "metrics.json", calculated)
    save(output, "failure_analysis.json", failures)
    save(output, "knowledge_tool_decision.json", knowledge)
    text = ["# OpenTelemetry 官方近真实环境 RCA v1 正式消融", "",
            "OpsPilot 没有企业生产数据。所有表格读取本次真实实验 artifact；负结果、API 失败和 fallback 均保留。", "",
            f"运行 `{output.name}`，状态 `{status['status']}`；保存的实际 RCA {len(raw_records)} 次，其中完整恢复后纳入正式配对统计 {len(records)} 次。失败/重试工件不删除，重复同一场景/模式/编号时采用最近完整 trial。工件 `{output.relative_to(ROOT).as_posix()}`。",
            "", "## Environment / 冻结配置", "",
            f"Upstream `{environment['upstream']['commit']}`；Demo {environment['upstream']['demo_image_version']}，Collector {environment['upstream']['collector_version']}。OpsPilot HEAD `{environment['opspilot_commit']}` 加不可变 source_manifest/source archive；未提交源码纳入快照，没有覆盖用户工作区。",
            f"Dataset `{dataset['dataset_version']}`；experiment `{config['experiment_version']}`；固定官方 Locust {dataset['load']['user_count']} 用户、spawn={dataset['load']['spawn_rate']}。release 所有时间参数保存在 config/dataset/各 trial 的 scenario.json。",
            f"真实 Planner 模型 `{config['llm']['model']}`，temperature={config['llm']['temperature']}，max_tokens={config['llm']['max_tokens']}，attempts={config['llm']['max_attempts']}；原始 response usage 已采集，未配置可靠单价，Estimated Cost=N/A。",
            "", "## Fault Dataset / Modes", "",
            "6 faults + Normal，Ground Truth 仅 Controller/scorer；Engine 只收到随机 ID symptom Alert 与脱敏真实工具数据。changes.query 不读取 flags。",
            "Full Adaptive：现有 Coordinator/L1/Gate + 真实 LLM Planner，记录未触发 Planner 的 Gate 提前结束和 fallback；Fixed Full：串行实际执行全部 13 工具、同一 Evidence/Ranker/Gate，无 LLM、无 Expert 调用；No-L2：同一真实 Planner，Expert budget=0。所有模式使用相同确定性排序和摘要，不使用 LLM 改写候选。",
            "配对设计：每次注入/恢复周期，三个模式分别对同一个固定 Alert 时间窗发起真实 backend queries，顺序按重复号轮换。21 生命周期对应 63 次真实 RCA；不是把一个结果复制三份。工具请求次数、底层 retries、API calls 与语义 Tool calls 分开保存。Controller 为测量完整遥测另做的全工具采集不计入任何模式的 RCA 调用成本。",
            "", "## Overall results", "",
            "| Mode | Scored | Fault Top1 | Fault Top3 | Normal | Gate | Budget | Degraded | Avg Tool | Avg Expert | Avg rounds | Avg LLM | P50 ms | P95 ms |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for mode, data in calculated.items():
        value = data["overall"]
        text.append(f"| {mode} | {value['scored']} | {rate(value['top1'])} | {rate(value['top3'])} | {rate(value['no_fault_accuracy'])} | {rate(value['gate_pass_rate'])} | {rate(value['budget_exhausted_rate'])} | {rate(value['degraded_run_rate'])} | {number(value['avg_tool_calls'])} | {number(value['avg_expert_calls'])} | {number(value['avg_investigation_rounds'])} | {number(value['avg_llm_calls'])} | {number(value['p50_latency_ms'])} | {number(value['p95_latency_ms'])} |")
    text += ["", "Fixed 的 Budget=0 表示没有自适应预算中止；执行 13 工具超出 Adaptive 的 8 工具预算是本 baseline 定义，不能声称相同成本约束。Gate pass 与 Top1 正确是独立指标。", "", "## Per-scenario results", "",
             "| Scenario | Mode | Fault observed | Top1 | Top3 | Normal | Gate | Budget | Degraded | Tool/Expert/Rounds/LLM avg | P50/P95 ms |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for scenario in dataset["scenarios"]:
        for mode in MODES:
            value = calculated[mode]["per_scenario"].get(scenario["scenario_id"])
            if value:
                text.append(f"| {scenario['scenario_id']} | {mode} | {value['fault_observation_confirmed']} | {rate(value['top1'])} | {rate(value['top3'])} | {rate(value['no_fault_accuracy'])} | {rate(value['gate_pass_rate'])} | {rate(value['budget_exhausted_rate'])} | {rate(value['degraded_run_rate'])} | {'/'.join(number(value[k]) for k in ('avg_tool_calls','avg_expert_calls','avg_investigation_rounds','avg_llm_calls'))} | {number(value['p50_latency_ms'])}/{number(value['p95_latency_ms'])} |")
    text += ["", "## Adaptive vs Fixed / L2 Expert ablation", ""]
    fixed = calculated["fixed_full"]["overall"]
    comparison = {}
    for mode in ("full_adaptive", "adaptive_no_l2"):
        value = calculated[mode]["overall"]
        tool_reduction = (1 - value["avg_tool_calls"] / fixed["avg_tool_calls"]) * 100 if fixed["avg_tool_calls"] and value["avg_tool_calls"] is not None else None
        difference = (value["top1"]["rate"] - fixed["top1"]["rate"]) * 100 if value["top1"]["rate"] is not None and fixed["top1"]["rate"] is not None else None
        comparison[mode] = {"fault_top1_difference_percentage_points": difference, "tool_call_reduction_percent": tool_reduction,
                            "p50_latency_difference_ms": value["p50_latency_ms"] - fixed["p50_latency_ms"] if value["p50_latency_ms"] is not None and fixed["p50_latency_ms"] is not None else None,
                            "p95_latency_difference_ms": value["p95_latency_ms"] - fixed["p95_latency_ms"] if value["p95_latency_ms"] is not None and fixed["p95_latency_ms"] is not None else None}
        text.append(f"- {mode} 相对 Fixed：Fault Top1 差值 {number(difference)} 个百分点；Tool calls 减少 {number(tool_reduction)}%；P50/P95 latency 差值 {number(comparison[mode]['p50_latency_difference_ms'])}/{number(comparison[mode]['p95_latency_difference_ms'])}ms。")
        text.append(f"- {mode} 真正使用 Planner 的运行 {value['llm_used_runs']}/{value['scored']}，fallback 运行 {value['fallback_runs']}；API 成功/失败={value['api_successes']}/{value['api_failures']}，有效决策={value['planner_successes']}，fallback 决策={value['planner_fallbacks']}；已记录 usage {value['usage_available_calls']}/{value['api_calls']}，tokens={value['tokens']}。")
    save(output, "comparisons.json", comparison)
    populated = {mode: calculated[mode]["overall"] for mode in MODES if calculated[mode]["overall"]["scored"]}
    if populated:
        best_accuracy = max((v["top1"]["rate"] or 0) for v in populated.values())
        best_modes = [m for m, v in populated.items() if (v["top1"]["rate"] or 0) == best_accuracy]
        minimum_tools = min(v["avg_tool_calls"] for v in populated.values())
        cheapest_tools = [m for m, v in populated.items() if v["avg_tool_calls"] == minimum_tools]
        fastest = min(populated, key=lambda m: populated[m]["p50_latency_ms"])
        text += ["", f"本次 Fault Top1 最高：{best_modes}；平均 Tool calls 最低：{cheapest_tools}；P50 latency 最低：{fastest}。Fixed LLM calls=0；这些成本维度不能未经单价配置合成现金成本。"]
        a, c = calculated["full_adaptive"]["overall"], calculated["adaptive_no_l2"]["overall"]
        if a["top1"]["rate"] is not None and c["top1"]["rate"] is not None:
            text.append(f"L2 消融：Full 相对 No-L2 的 Fault Top1 差值={(a['top1']['rate']-c['top1']['rate'])*100:.2f} 个百分点；平均 Expert calls={number(a['avg_expert_calls'])}/{number(c['avg_expert_calls'])}。按场景表核对收益，三次重复不证明显著性。")
    expert_usage = []
    for record in records:
        events = read_json(Path(record["path"]) / "rca/events.json")
        expert_usage.append({"scenario_id": record["scenario_id"], "repetition": record["repetition"], "mode": record["mode"],
                             "invoked_experts": events["invoked_experts"], "expert_calls": events["expert_budget_used"],
                             "artifact": str(Path(record["path"]) / "rca/events.json")})
    save(output, "expert_usage.json", expert_usage)
    text += ["", "各场景有价值/更稳定的判断以逐场景 Top1/Top3、调用数和延迟为依据。Expert 是按需实际调用，不能将 Fixed 的直接域工具查询算作 Expert 调用。三个重复仅为初步配对结果，没有统计显著性结论；LLM API 数量降低不等于现金成本降低。", "", "## Failure analysis", "",
             f"错误 Top1 {failures['wrong_top1_cases']} 次；主要观测条件计数 `{failures['primary_counts']}`；多标签计数 `{failures['label_counts']}`。完整证据与对应文件见 failure_analysis.json/md。自动分类不调用 LLM，条件共现不证明唯一因果，无法确定的因果标为 unknown。",
             "", "## Limitations", "",
             "类别评分，不宣称精确服务定位。paymentUnreachable 的连接/DNS ERROR 不等于 timeout；DB_SLOW_QUERY 不等于锁竞争机制；内存增长不等于 OOM，email 人工恢复重启另记。Controller 的无故障 baseline 与 Engine 包含稳定阶段的历史窗口不同；保持前版 release 窗口冻结，后续单独消融。",
             "部分 deployment/topology/DB/cache 指标缺失；trace/log 窗口限额和稀疏流量影响采样。官方负载还包含未部署可选 agent 的 /prompt 背景请求失败。Normal 仅表示固定负载下的无故障注入状态。三个模式共享同一生命周期但实际发起查询，查询顺序轮换仍不能排除异步采样差异。",
             "", "## Knowledge Tool decision", "", knowledge["decision"], "", knowledge["reason"], "",
             "当前 Evidence/错例逐项见 failure_analysis.json；外部知识候选仅为待核验设想，未证明实时 Evidence 无法区分，也没有把 feature flags 或 benchmark 答案当作知识库。", knowledge["verification_plan"], "", "本任务没有开发/接入 EvalRAG 或 Knowledge Tool。"]
    destination = report_path or ROOT / "reports/otel_demo_rca_v1.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(text) + "\n", encoding="utf-8")
    failure_text = ["# Artifact-based failure analysis", "", "以下为观测条件，不是 LLM 猜测或确定的唯一原因。"]
    for failure in failures["failures"]:
        failure_text += ["", f"## {failure['mode']} / {failure['scenario_id']} #{failure['repetition']}",
                         f"预测 {failure['predicted']}；期望 {failure['expected']}；primary={failure['primary_observed_condition']}；causal diagnosis=unknown。"]
        failure_text += [f"- {LABELS[fact['label']]}：{fact['fact']}；artifact `{fact['artifact']}`。" for fact in failure["facts"]]
    (output / "failure_analysis.md").write_text("\n".join(failure_text) + "\n", encoding="utf-8")
    (ROOT / "artifacts/failure_analysis.json").write_text((output / "failure_analysis.json").read_text(encoding="utf-8"), encoding="utf-8")
    (ROOT / "artifacts/failure_analysis.md").write_text("\n".join(failure_text) + "\n", encoding="utf-8")
    decision_text = ["# Knowledge Tool Decision", "", knowledge["decision"], "", knowledge["reason"],
                     f"来源 `{output.relative_to(ROOT)}`，实际失败 Top1 {failures['wrong_top1_cases']} 次。", "", "## 判断标准", "",
                     "必须同时满足：多个真实错例；正常采到 metrics/logs/traces；实时 Evidence 无法区分；独立历史 incident/runbook 确有区分信息；不是简单 Tool/Evidence/Planner bug。未核验的条件记为未证实，不当作已满足。",
                     "", "## 当前 Case / Evidence / 错误条件", ""]
    for failure in failures["failures"]:
        decision_text += [f"- {failure['mode']}/{failure['scenario_id']} #{failure['repetition']}：预测 {failure['predicted']}，观测条件 {failure['labels']}；Evidence 与 facts 见 `{failure['path']}`。"]
        decision_text += [f"  Evidence `{e['evidence_type']}`：{e['fact']}。" for e in failure["evidence"][:3]]
    decision_text += ["", "## 什么外部知识可能帮助", "", *knowledge["external_knowledge_candidates"], "",
                     "这些是待核验候选，没有独立 incident/runbook corpus，也未证明实时 Evidence 不够。不能用已知注入机制作为实时 Agent 的答案。", "", "## 后续验证", "", knowledge["verification_plan"], "", "不直接实现 EvalRAG/Knowledge Tool。"]
    (ROOT / "artifacts/knowledge_tool_decision.md").write_text("\n".join(decision_text) + "\n", encoding="utf-8")
    resume = ["# 可追溯实验数字草稿（不是简历文案）", "", f"正式状态 `{status['status']}`；配置 `{output / 'config.json'}`；dataset `{output / 'dataset.json'}`；上游 `{environment['upstream']}`。"]
    for mode in MODES:
        value = calculated[mode]["overall"]
        resume += ["", f"## {mode}", f"Fault Top1={rate(value['top1'])}；Top3={rate(value['top3'])}；Normal={rate(value['no_fault_accuracy'])}。",
                   f"Avg Tool/Expert={number(value['avg_tool_calls'])}/{number(value['avg_expert_calls'])}；P50/P95={number(value['p50_latency_ms'])}/{number(value['p95_latency_ms'])}ms。",
                   f"来源 `{output / 'metrics.json'}` / `{output / 'records.json'}`，只能引用此模式与版本，fallback 运行={value['fallback_runs']}。"]
    resume += ["", f"调用差值来源 `{output / 'comparisons.json'}`；Recovery 逐次事实来源 `{output / 'pairs.json'}` 与各 trial recovery/；不能把人工重启描述成自愈。", "", "没有企业生产数据，不宣称生产环境验证。"]
    (ROOT / "artifacts/resume_evidence.md").write_text("\n".join(resume) + "\n", encoding="utf-8")
    return calculated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    generate(args.run.resolve(), args.output)
    print("Experiment reports generated from persisted artifacts")


if __name__ == "__main__":
    main()
