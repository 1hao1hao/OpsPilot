"""Append auditable release completeness, paired comparisons and per-run model usage."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opspilot.otel_benchmark.experiment_report import aggregate, completed_records
from opspilot.otel_benchmark.modes import MODES, read_json


def rate(metric):
    return f"{metric['numerator']}/{metric['denominator']}" if metric["denominator"] else "N/A"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--report", type=Path, default=Path("reports/otel_demo_rca_v1.md"))
    args = parser.parse_args()
    run = args.run.resolve()
    status = read_json(run / "status.json")
    assert status["status"] == "completed", "Cannot finalize an unfinished release"
    records = completed_records(read_json(run / "records.json"))
    dataset = read_json(run / "dataset.json")
    config = read_json(run / "config.json")
    metrics = read_json(run / "metrics.json")
    expected = {(s["scenario_id"], m): config["repeats"] for s in dataset["scenarios"] for m in MODES}
    actual = Counter((r["scenario_id"], r["mode"]) for r in records)
    assert dict(actual) == expected, "Actual complete repeat matrix does not match the frozen request"
    assert len(records) == len(dataset["scenarios"]) * len(MODES) * config["repeats"]
    pairs = read_json(run / "pairs.json")
    safe_pairs = [p for p in pairs if p["status"] == "completed" and p.get("recovery_health")
                  and p.get("recovery_queries_success") and p.get("symptom_recovery_confirmed") is not False]
    confirmed_faults = [p for p in safe_pairs if p.get("fault_observed") is True]
    assert len(safe_pairs) == 21 and len(confirmed_faults) == 18
    audit = {"client_timezone": "Asia/Shanghai", "reported_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
             "requested_repeats": config["repeats"], "actual_complete_diagnoses": len(records),
             "actual_saved_diagnoses": len(read_json(run / "records.json")), "lifecycle_attempts": len(pairs),
             "complete_lifecycles": len(safe_pairs), "observable_fault_lifecycles": len(confirmed_faults),
             "repeat_matrix": [{"scenario_id": s, "mode": m, "actual_repeats": n} for (s, m), n in actual.items()],
             "report_helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (run / "release_completeness.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = ["", "## 完整性复核与逐次真实 LLM 使用", "",
            f"客户端时区 Asia/Shanghai：{audit['reported_at']}；真实完整 RCA={len(records)}，完整生命周期={len(safe_pairs)}，确认产生故障效果的生命周期={len(confirmed_faults)}。逐场景/模式 repeats 在 release_completeness.json 中验证，未用支持 repeat=3 冒充实际重复。",
            "", "### Controller Ground Truth mapping", "",
            "| Scenario | 实际 flag / variant | operational category | root service | mechanism / gap |",
            "| --- | --- | --- | --- | --- |"]
    for s in dataset["scenarios"]:
        control, truth = s["fault_control"], s["ground_truth"]
        text.append(f"| {s['scenario_id']} | {control['flag'] + '/' + control['variant'] if control else 'all off'} | {truth['root_cause_type']} | {truth['root_service']} | {truth['mechanism']}; {truth.get('taxonomy_note', '')} |")
    text += ["", "### 哪些场景 Adaptive 有收益 / Fixed 更稳定", "",
             "下表只描述本轮实际配对差值，不声称三个重复具备统计显著性或唯一因果。", "",
             "| Scenario | Full / Fixed / No-L2 Top1（Normal 为 NO_FAULT） | Full 相对 Fixed 工具降幅 | Full / Fixed P50 ms | Full / No-L2 Avg Expert |",
             "| --- | --- | --- | --- | --- |"]
    for s in dataset["scenarios"]:
        values = [metrics[m]["per_scenario"][s["scenario_id"]] for m in MODES]
        a, b, c = values
        key = "top1" if s["fault_control"] else "no_fault_accuracy"
        reduction = (1 - a["avg_tool_calls"] / b["avg_tool_calls"]) * 100
        text.append(f"| {s['scenario_id']} | {' / '.join(rate(v[key]) for v in values)} | {reduction:.2f}% | {a['p50_latency_ms']:.2f}/{b['p50_latency_ms']:.2f} | {a['avg_expert_calls']:.2f}/{c['avg_expert_calls']:.2f} |")
    text += ["", "所有模式都用串行工具执行来控制执行策略；本轮没有评估 Fixed 全量并行查询，因此不代表最优 Fixed 延迟。No-L2 未采集域工具可能是预算=0 的预期限制，failure label F 仅表示遗漏区分性工具，不能单凭它断言 Planner 做了非法决策。",
             "", "### 真正 LLM / fallback / Planner 未触发的子集", "",
             "模式名称表示配置的策略；逐次是否真正用了 LLM 如下。含 fallback 的运行不会描述为纯 LLM 成功；Gate 在 L1 结束时也不会伪造一次模型调用。", "",
             "| Mode | 子集 | runs | Fault Top1 | Fault Top3 | Normal | Avg Tool | P50/P95 ms |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    subsets = {}
    for mode in ("full_adaptive", "adaptive_no_l2"):
        rows = [r for r in records if r["mode"] == mode]
        groups = {"real_llm_without_fallback": [r for r in rows if r["llm"]["llm_used"] and not r["llm"]["fallback_occurred"]],
                  "with_fallback": [r for r in rows if r["llm"]["fallback_occurred"]],
                  "planner_not_triggered": [r for r in rows if not r["llm"]["llm_used"] and not r["llm"]["fallback_occurred"]]}
        subsets[mode] = {name: aggregate(group) for name, group in groups.items()}
        for name, value in subsets[mode].items():
            tool = f"{value['avg_tool_calls']:.2f}" if value["avg_tool_calls"] is not None else "N/A"
            latency = f"{value['p50_latency_ms']:.2f}/{value['p95_latency_ms']:.2f}" if value["p50_latency_ms"] is not None else "N/A"
            text.append(f"| {mode} | {name} | {value['scored']} | {rate(value['top1'])} | {rate(value['top3'])} | {rate(value['no_fault_accuracy'])} | {tool} | {latency} |")
    (run / "true_llm_subset_metrics.json").write_text(json.dumps(subsets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text += ["", "### 每次调用与候选", "",
             "| Scenario/repeat | Mode | Top1 correct | Predicted | Tool/Expert | LLM used | fallback | API ok/fail | tokens（已返回 usage） | latency ms |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in records:
        score, llm = r["score"], r["llm"]
        text.append(f"| {r['scenario_id']}/{r['repetition']} | {r['mode']} | {score['top1']} | {', '.join(score['predicted'])} | {score['tool_calls']}/{score['expert_calls']} | {llm['llm_used']} | {llm['fallback_occurred']} | {llm['api_successes']}/{llm['api_failures']} | {llm['tokens']['total_tokens']} | {score['latency_ms']:.2f} |")
    text += ["", "Token 合计只包括已返回 usage 的响应；失败时未返回 usage 不代表零消费。现金成本未估计。逐次 raw response 和请求在各模式 llm/，未保存授权头或 API key。",
             "", "### 当前实时 Evidence 的限制与 Knowledge 决策依据", "",
             "Normal 的全部故障 off 不表示所有后台请求无错误。现有 traces.query 把超过阈值的慢 span 支持到 RPC_TIMEOUT，需要结合 RPC 协议状态和业务 scope 核查；长 span 不能单独证明协议 timeout。Fixed 的全局 Redis hit-rate 标量没有域基线变化上下文，固定阈值可能把稳定的正常低命中率当故障。控制器另存 baseline，不能据此声称 Engine 已看到同样基线。",
             "这些是待核验的实时采集/证据/排名/工具选择问题；本轮没有经验证的独立历史 incident/runbook corpus，不能跳过基础问题直接建议 EvalRAG。Knowledge Tool 决策与错例路径见 artifacts/knowledge_tool_decision.md 和 failure_analysis.json。",
             "", "### 版本与失败现场", "",
             "1.0.0 在 CPU gauge 恢复检查中中止，单独保留于 artifacts/otel_demo_rca_v1/20261005T032959Z-d57607/ 和 reports/otel_demo_rca_v1_interrupted.md。1.0.1 全量重新执行；只在新冻结前修复通用 JSON 请求声明、CPU counter rate 与匿名控制日志隔离，未逐 Case 改 Planner/Gate/Ranker、负载、window、variant 或答案。"]
    if len(read_json(run / "records.json")) > len(records):
        text += ["", "额外中断尝试单独保留在 records.json、pairs.json 和 resume_history/。进程退出原因未证实；没有恢复确认的诊断不进入正式统计，同一场景在原冻结参数下实际重新注入并诊断。正式 repeat matrix 仅包含已完整恢复的结果。"]
    report = args.report.resolve()
    report.write_text(report.read_text(encoding="utf-8") + "\n".join(text) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
