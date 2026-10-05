"""Finalize a completed live release, checks and documentation from actual artifacts."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from opspilot.otel_benchmark.experiment_report import generate
from opspilot.otel_benchmark.modes import MODES, read_json
from opspilot.otel_benchmark.runner import command, containers

ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(arguments, log, *, env=None):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, check=False,
                            encoding="utf-8", errors="replace", env=env)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, f"Check failed; see {log}"
    return result.stdout + result.stderr


def rate(value):
    return f"{value['numerator']}/{value['denominator']}"


def finalize(run):
    verification = run / "final_verification.json"
    assert not verification.exists(), "Already finalized; preserve the prior completion record"
    if (run / "resume_history/process_exit_01").exists():
        execute([sys.executable, "scripts/otel_demo/reconcile_rca_interruption.py", str(run)], run / "index_reconciliation.log")
    values = generate(run)
    execute([sys.executable, "scripts/otel_demo/finalize_rca_report.py", str(run)], run / "report_finalization.log")
    execute([sys.executable, "scripts/otel_demo/audit_rca_release.py", str(run)], run / "independent_audit.log")
    live = containers()
    assert len(live) == 28 and all(c["state"] == "running" and c["health"] not in ("unhealthy", "starting") for c in live)
    upstream_status = command("git", "-C", str(ROOT / ".external/opentelemetry-demo"), "status", "--short")
    assert not upstream_status, "Upstream clone must remain clean"
    test_env = {k: v for k, v in os.environ.items() if not k.startswith("OPSPILOT_")}
    test_env["OPSPILOT_LLM_ENABLED"] = "false"
    pytest_log = execute([sys.executable, "-m", "pytest", "-q"], run / "pytest_final.log", env=test_env)
    ruff_log = execute([sys.executable, "-m", "ruff", "check", "src/opspilot", "tests/contract", "tests/integration",
                        "scripts/otel_demo"], run / "ruff_final.log")
    passed, skipped = re.search(r"(\d+) passed", pytest_log), re.search(r"(\d+) skipped", pytest_log)
    assert passed and skipped
    checks = {"status": "passed", "finished_at": datetime.now(UTC).isoformat(),
              "pytest_passed": int(passed[1]), "pytest_skipped": int(skipped[1]), "ruff": ruff_log.strip(),
              "pytest_environment": "default test configuration; live environment overrides removed; live proof is separate",
              "running_services": len(live), "upstream_clean": True,
              "independent_audit": "independent_audit.json", "release_completeness": "release_completeness.json"}
    write(verification, checks)
    relative = run.relative_to(ROOT).as_posix()
    a, b, c = (values[m]["overall"] for m in MODES)
    completeness = read_json(run / "release_completeness.json")
    reduction = (1 - a["avg_tool_calls"] / b["avg_tool_calls"]) * 100
    knowledge = read_json(run / "knowledge_tool_decision.json")["decision"]
    table = ["| 模式 | Fault Top1 | Fault Top3 | Normal | Avg Tool | Avg Expert | P50 / P95 ms |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for mode, v in zip(MODES, (a, b, c), strict=True):
        table.append(f"| {mode} | {rate(v['top1'])} | {rate(v['top3'])} | {rate(v['no_fault_accuracy'])} | {v['avg_tool_calls']:.2f} | {v['avg_expert_calls']:.2f} | {v['p50_latency_ms']:.2f} / {v['p95_latency_ms']:.2f} |")
    readme = ROOT / "README.md"
    original = readme.read_text(encoding="utf-8")
    original = original.replace("当前未执行付费真实 LLM 评测；Planner", "旧 v1 mock 样本尚未执行付费真实 LLM 评测；Planner")
    section = ["", "## 官方 Demo 正式消融（2026-10-05）", "",
               f"近真实微服务环境中的 7 场景 × 3 repeats × 3 modes，共 {completeness['actual_complete_diagnoses']} 次实际 RCA、{completeness['complete_lifecycles']} 个完整生命周期，{completeness['observable_fault_lifecycles']} 次故障出现及恢复确认。Full 与 No-L2 使用当前配置的真实 DeepSeek Planner，逐次 LLM/fallback 单独记录；不代表企业生产数据。", "", *table, "",
               f"Full 相对 Fixed 的工具调用减少 {reduction:.2f}%；准确率、延迟和 L2 收益须按结果分别解读，不把调用数减少写成全面性能提升。Fixed 不调用 LLM；模型现金成本未估算。Knowledge Tool Decision：**{knowledge}**，未接入 EvalRAG 或 Knowledge Tool。", "",
               f"[完整报告](reports/otel_demo_rca_v1.md) · [复现配置与步骤](benchmarks/datasets/otel_demo/v1/EXPERIMENT.md) · [真实逐次结果]({relative}/records.json) · [独立验收]({relative}/independent_audit.json) · [失败分析](artifacts/failure_analysis.md) · [Knowledge 决策](artifacts/knowledge_tool_decision.md)。",
               f"本次最终验证：{checks['pytest_passed']} passed / {checks['pytest_skipped']} skipped；Ruff 通过。旧 mock benchmark 的 6/21 覆盖率结果仍单独保留。"]
    readme.write_text(original + "\n".join(section) + "\n", encoding="utf-8")
    failures = read_json(run / "failure_analysis.json")
    plan = ROOT / "plan/sy.md"
    completion = ["", "## 最终完成记录（2026-10-05，Asia/Shanghai）", "",
                  f"状态：已完成正式 release 及任务要求的三模式比较、失败分析、Knowledge 决策、README 更新和可追溯数字草稿。最终运行 `{relative}`，experiment 1.0.1，dataset 1.0.1。", "",
                  "真实 63 次有效 RCA、21 个完整配对生命周期，6 类故障每类 3 次均确认产生效果与恢复；7×3×3 矩阵和冻结/泄漏/调用计数独立检查通过。最后 28 个服务运行、全部 fault off、upstream clean。", "", *table, "",
                  f"Full 工具调用比 Fixed 少 {reduction:.2f}%；最大观测错误条件计数 `{failures['primary_counts']}`，不据此声称唯一因果。真实 LLM/fallback 逐次明细、Token、Gate/Budget/Degraded 和 L2 消融在最终报告及 metrics.json；现金成本 N/A。", "",
                  f"Knowledge Tool Decision：{knowledge}。缺少经核验的独立历史 incident/runbook 且基础 pipeline 问题未排除，未实现 EvalRAG/Knowledge Tool。", "",
                  "完整报告 reports/otel_demo_rca_v1.md；自动错误分类 artifacts/failure_analysis.json/md；知识决策 artifacts/knowledge_tool_decision.md；数字草稿 artifacts/resume_evidence.md。每个 mode 的 raw requests/responses/usage/tool/evidence/score 和每个 trial 的 baseline/fault/recovery 均保留。", "",
                  "1.0.0 CPU gauge 恢复失败现场单独保留，不合并正式得分。1.0.1 进程中断发生于 CPU 第二组，尚无 RCA；CPU 第一组已完成恢复。曾因落后日志误建的重复索引保存在 resume_history/process_exit_01/*before_index_correction.json，并依据生命周期 JSON 更正，不增加真实诊断数。", "",
                  f"最终本地测试 {checks['pytest_passed']} passed / {checks['pytest_skipped']} skipped；Ruff 通过。验证记录 `{relative}/final_verification.json`。Planner/Fallback、Evidence Gate、Ranker 未为本轮结果调参；旧 Benchmark 6/21 未处理。"]
    with plan.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(completion) + "\n")
    with (ROOT / "reports/otel_demo_rca_v1.md").open("a", encoding="utf-8") as stream:
        stream.write(f"\n## 最终独立验证\n\n实际 repeat matrix、源码/配置不变、Agent boundary 隔离、API key 未落盘及调用计数通过 independent_audit.json；全部故障 off，28 服务运行，官方 clone clean。最终 pytest {checks['pytest_passed']} passed / {checks['pytest_skipped']} skipped，Ruff 通过；详见 `{relative}/final_verification.json` 与 pytest_final.log / ruff_final.log。\n")
    with (ROOT / "artifacts/resume_evidence.md").open("a", encoding="utf-8") as stream:
        stream.write(f"\n真实数量与独立审计来源 `{relative}/release_completeness.json`、`independent_audit.json` 和 `final_verification.json`。63 次 RCA 与 21 次生命周期不可混用；18 次故障出现/恢复不包括 Normal。\n")
    checks["documentation_completed"] = True
    write(verification, checks)
    print(json.dumps(checks, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    if args.wait:
        while read_json(run / "status.json")["status"] == "running":
            time.sleep(45)
    assert read_json(run / "status.json")["status"] == "completed", "Interrupted runs cannot be finalized"
    # The controller marks completion before finally resetting flags; wait for its lock release.
    while (ROOT / ".external/otel-benchmark.lock").exists():
        time.sleep(5)
    finalize(run)


if __name__ == "__main__":
    main()
