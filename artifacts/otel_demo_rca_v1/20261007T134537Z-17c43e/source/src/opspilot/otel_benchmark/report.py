"""Generate the benchmark report exclusively from persisted experiment artifacts."""

import argparse
import json
from pathlib import Path

from .runner import ROOT
from .scoring import summarize


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fraction(metric):
    return f"{metric['numerator']}/{metric['denominator']}" if metric["denominator"] else "N/A"


def write_report(paths, destination):
    text = ["# OpenTelemetry 官方近真实微服务环境 RCA Benchmark", "",
            "OpsPilot 没有企业生产数据。本实验使用官方 Astronomy Shop、真实 Locust、feature-flag 故障注入和真实 telemetry；未运行真实 LLM 大规模实验。",
            "前置 task1.5 已完成，LIVE_INTEGRATION=PASS，见 `reports/otel_demo_live_smoke.md`。本报告所有分数和观测来自以下保存的工件；不会将流程成功写成诊断正确。", ""]
    per_run = {}
    for path in paths:
        dataset = read(path / "dataset.json")
        environment = read(path / "environment.json")
        records = read(path / "trials.json")
        summary = summarize(records)
        per_run[path.name] = summary
        text.extend([f"## {environment['profile']}：{path.name}", "",
                     f"Dataset `{dataset['dataset_version']}`，SHA-256 `{environment['dataset_sha256']}`；实际 repeat override={environment['repeat_override']}，Normal 按 profile 真实重复。",
                     f"Upstream `{dataset['upstream_version']['commit']}`；Demo {dataset['upstream_version']['demo_image_version']}；Collector {dataset['upstream_version']['collector_version']}。负载固定 {dataset['load']['user_count']} 用户、spawn rate {dataset['load']['spawn_rate']}。",
                     "",
                     "| Scenario | 已执行/完整生命周期 | 故障效果确认 | Fault Top1 | Fault Top3 | Normal NO_FAULT | Gate pass | Budget exhausted | Degraded |",
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        for scenario, values in summary["by_scenario"].items():
            text.append(f"| {scenario} | {values['attempts']}/{values['completed']} | {values['fault_observation_confirmed']} | "
                        f"{fraction(values['top1'])} | {fraction(values['top3'])} | {fraction(values['no_fault_accuracy'])} | "
                        f"{fraction(values['gate_pass_rate'])} | {fraction(values['budget_exhausted_rate'])} | {fraction(values['degraded_run_rate'])} |")
        total = summary["overall"]
        text.extend(["", f"Fault 总计：Top1 **{fraction(total['top1'])}**，Top3 **{fraction(total['top3'])}**；Normal **{fraction(total['no_fault_accuracy'])}**。",
                     f"总体：完整生命周期 {total['completed']}/{total['attempts']}，已评分 {total['scored']}；Gate pass {fraction(total['gate_pass_rate'])}，Budget exhausted {fraction(total['budget_exhausted_rate'])}，Degraded {fraction(total['degraded_run_rate'])}。",
                     f"已确认产生可观测差异的 Fault 子集：Top1 {fraction(total['verified_fault_top1'])}，Top3 {fraction(total['verified_fault_top3'])}。未确认故障差异的分数仍在原始 score 中保存，但不能代表有效故障诊断准确率。",
                     "", "### 实际 trial、遥测与失败", "",
                     "| Case/repeat | 状态 | 实际预测 Top-K | tool/expert/rounds | RCA latency ms | 恢复 health/queries/symptom |",
                     "| --- | --- | --- | --- | --- | --- |"])
        for record in records:
            score = record.get("score") or {}
            text.append(f"| {record['scenario_id']}/{record['repetition']} | {record['status']} | {', '.join(score.get('predicted', []))} | "
                        f"{score.get('tool_calls', 'N/A')}/{score.get('expert_calls', 'N/A')}/{score.get('investigation_rounds', 'N/A')} | "
                        f"{score.get('latency_ms', 'N/A')} | {record.get('recovery_health')}/{record.get('recovery_queries_success')}/{record.get('symptom_recovery_confirmed', 'N/A')} |")
        for record in records:
            directory = Path(record["path"])
            text.extend(["", f"#### {record['scenario_id']} #{record['repetition']}", "", f"原始工件：`{directory.relative_to(ROOT).as_posix()}`。总实验时长 {record.get('duration_seconds', 0):.1f} 秒。"])
            if record.get("error"):
                text.append("失败：" + json.dumps(record["error"], ensure_ascii=False))
            observations_path = directory / "benchmark/observed_delta.json"
            if observations_path.exists():
                delta = read(observations_path)
                text.append(f"故障效果确认：{delta['fault_observed']}（Controller 的观测检查，不用于 Agent 预测）。")
                for key, value in delta["delta"].items():
                    text.append(f"- `{key}`：baseline={value.get('baseline')} → incident={value.get('fault')}。")
                for key, value in delta.get("trends", {}).items():
                    if key in ("cpu_usage", "memory_usage"):
                        text.append(f"- `{key}` 趋势：{json.dumps(value, ensure_ascii=False)}。")
            results_path = directory / "telemetry/tool_results.json"
            if results_path.exists():
                results = read(results_path)
                counts = {r["tool_name"]: len((r.get("data") or {}).get("observations", {}).get("traces", []))
                          if r["tool_name"] == "traces.query" else len((r.get("data") or {}).get("observations", {}).get("logs", []))
                          for r in results if r["tool_name"] in ("logs.query", "traces.query")}
                text.append("真实采样数量：" + json.dumps(counts, ensure_ascii=False) + "；其他全部 normalized/domain 数据及原始请求参见 telemetry/。")
            audit_path = directory / "benchmark/isolation_audit.json"
            if audit_path.exists():
                text.append("隔离审计：" + json.dumps(read(audit_path), ensure_ascii=False))
            events_path = directory / "rca/events.json"
            if events_path.exists():
                trace = read(events_path)
                text.append("Engine 实际选择工具：" + ", ".join(trace["executed_tools"]) + "；Controller 的全工具采集不计入 RCA tool_calls。")
            evidence_path = directory / "rca/evidence.json"
            if evidence_path.exists():
                for evidence in read(evidence_path)[:4]:
                    text.append(f"- Evidence `{evidence['evidence_type']}` ({evidence['source_name']})：{evidence['fact'][:500]}。")
        text.extend(["", "### 本次 Ground Truth mapping", "",
                     "| Scenario | flag/variant（仅 Controller） | operational category | root_service | mechanism / taxonomy note |",
                     "| --- | --- | --- | --- | --- |"])
        for scenario in dataset["scenarios"]:
            control = scenario["fault_control"]
            truth = scenario["ground_truth"]
            text.append(f"| {scenario['scenario_id']} | {control['flag'] + '/' + control['variant'] if control else 'all off'} | "
                        f"{truth['root_cause_type']} | {truth['root_service']} | {truth['mechanism']}; {truth.get('taxonomy_note', '')} |")
    text.extend(["", "## 隔离、复现与限制", "",
                 "Alert 由 service_name、alert_type、severity、description allowlist 单独构造，随机 ID、无 signals/labels、无 scenario 或答案。工具参数仅含此 Alert；控制元数据及明确注入日志在 ToolResult/Evidence 前过滤，邮箱脱敏，原始响应仅在 Controller 工件保存。现有 changes.query 不访问 flagd。泄漏自动测试与实际每次运行的断言均执行，审计记录过滤数量。",
                 "Fault Controller 使用已核对 upstream 原子 write-then-rename API，独立 runtime flags 副本；写入后轮询 flagd Connect evaluation 的 variant，保存 before/requested/confirmed。各 trial 前后及异常路径 reset，锁防止并发注入；最后 final/flags.json 保存关闭状态。正常退出已关闭全部故障。email 的人工恢复重启与实际 OOM 区分。",
                 "RCA 使用现有 OpsPilotWorkflow/AdaptiveInvestigator，LLM 禁用、fallback 保持原逻辑。没有每场景诊断规则，没有根据答案修改 confidence，没有调整 Gate/Ranker/Planner/Runtime。评分只比较 category；root_service 存档，不宣称精确服务定位。",
                 "`telemetry/raw/`、prometheus_queries、normalized metrics/logs/traces、rca/report/events/tool_results/evidence、benchmark/ground_truth/score 和 environment/source archive 可复查每个判对/错。fixtures 标明实际采集源、SHA、时间、scenario 和 artifact；未修改实测值制造理想答案。",
                 "telemetry gap：无真实部署/事件/topology inventory；单 PostgreSQL 无 replica；active_connections 缺失；Valkey maxmemory/百分比与 hotkeys 缺失；部分 RPC 缺协议状态，不能伪造 timeout=0；ad 缺 container resource series，CPU 使用 JVM 比例。每次 trace/log 有窗口和采样限额，未观察不等于全量系统中绝不存在。",
                 "窗口限制：Controller 保存故障启用前的独立 baseline；Engine 沿用现有 provider 的告警前历史窗口，其中包含故障稳定阶段，因而部分历史基准已混入故障观测。两者不能混同，后续应独立消融窗口设计，不能据此回写本次预测或分数。",
                 "taxonomy gap：downstream-unavailable 的 UNAVAILABLE/连接/DNS 错误不等于 timeout，应核对实际 span/log。DB flag 的机制为锁竞争，而 DB_SLOW_QUERY 表示数据库延迟/瓶颈；当前 normalized Tools 不稳定暴露锁等待身份，未新增 DB_LOCK_CONTENTION 或专用规则。memory trend 不等于 OOM。",
                 "背景环境 gap：upstream Locust 包含可选 agent 的 /prompt 请求，但 core/full/observability 没有部署可选 AI layer，存在背景请求失败，未调用真实 LLM 补齐该服务。Normal ground truth 针对 frontend 的无注入状态，不宣称所有后台请求均无错误；fixed load/task mix 在同一 profile 全部案例保持相同。未来可在独立版本控制这项因素，不回写本次结果。",
                 "下一阶段消融需先评估 Normal false positives、降速/延迟变化的证据方向、独立 Evidence 来源和 Gate 预算、域工具/服务 scope 与 taxonomy gap。不可通过场景关键词或答案调规则。单次 Fault 和重复 Normal 仅为初步结果；release 默认支持 Fault repeat>=3，但本次实际次数以工件为准，未伪造三次重复。",
                 "", "测试和开发期中断/失败记录见 artifacts/otel_demo_benchmark*_tests.txt、probe/smoke 日志；正式统计仅使用上述明确列出的完整 runs。"])
    for path in paths:
        verification_path = path / "final/verification.json"
        if verification_path.exists():
            verification = read(verification_path)
            text.extend(["", f"## 最终复核：{path.name}", "",
                         f"复核时间 `{verification['checked_at']}`：{verification['container_count']} 个容器健康/运行={verification['all_healthy_running']}；活动故障={verification['active_fault_flags']}；upstream status={verification['upstream_status']!r}。",
                         f"与运行开始时源码归档比较，{verification['nonbenchmark_source_files_checked']} 个非 Benchmark 文件变化={verification['changed_nonbenchmark_source_files']}。原有工作区修改仍保留，此检查不表示相对于 git HEAD 干净。",
                         f"Alert/Tool arguments/ToolResult/Evidence/report 共 {verification['engine_boundary_files_checked']} 份 JSON 通过最终 key/value 隔离检查；该复核使用当前更严格的检查器，原始运行源码仍保存在 source/。"])
        tests_path = path / "final/test_results.json"
        if tests_path.exists():
            tests = read(tests_path)
            text.extend(["", f"测试：{tests['passed']} passed、{tests['skipped']} skipped、{tests['warnings']} warning；pytest exit={tests['pytest_exit_code']}，Ruff exit={tests['ruff_exit_code']}。",
                         f"{tests['completed_recorded_fixtures']} 份完整生命周期真实录制 fixture 用于离线回放；回放测试不计入 live 分数。原始测试日志 `{tests['pytest_log']}`。"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(text) + "\n", encoding="utf-8")
    return {"by_run": per_run, "combined_accuracy": None,
            "note": "Different profiles and dataset mappings are reported separately, never pooled"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/otel_demo_benchmark.md")
    args = parser.parse_args()
    print(json.dumps(write_report(args.runs, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
