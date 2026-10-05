# 正式 RCA / L2 消融

使用 `experiment.yaml` 固定三个模式、release 时间窗、官方 Locust 5 用户及 3 repeats。7 个 scenario × 3 repeats × 3 modes = 63 次实际 RCA。每个注入/恢复周期内，三个模式各自对相同 Alert 时间窗发起真实 backend queries，顺序按重复号轮换，共 21 个配对生命周期。不会复制同一 RCA 输出作为重复实验。

- `full_adaptive`：现有 Coordinator、L1、Gate、真实 LLM Planner、按需 L2。记录每次 API 成功/失败、Planner 有效决策及 fallback。若 Gate 在 L1 已结束，则该次没有 LLM 调用，会明确记录。
- `fixed_full`：实际串行执行现有全部 13 个工具，使用同一 Evidence/Ranker/Gate。无 LLM、无 Expert 调用；直接域工具查询不算 Expert 调用。
- `adaptive_no_l2`：与 Full 使用同一真实 Planner，只把 Expert budget 设为 0。

三个模式相同确定性排序和摘要；LLM 仅规划下一步，不修改候选排名。不会使用答案或 fault names 决定行动。Adaptive 的工具预算为 8，Fixed 的完整工具集为 13，两者成本约束不同，必须连同质量一起解释。

1.0.0 首轮在 CPU 恢复检查中安全中止：Docker/JVM 已空闲，但原容器 utilization gauge 仍偏高，12 次 RCA 与冻结源码均保留，不与新版本合并。1.0.1 在完整重跑前统一优先使用 scoped `container_cpu_usage_nanoseconds_total` 的一分钟 rate / 1e9，缺失时沿用已有 gauge/JVM fallback；未按场景选择数据源、调阈值或改答案。独立真实 CPU 开/关验证已确认增长及恢复；匿名 `problempattern` 控制日志也在 Evidence 前过滤。负载、窗口、模型、预算和 dataset mapping 不变。

PowerShell 在项目根目录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
. ./scripts/otel_demo/local_live_env.ps1
bash scripts/otel_demo/start_demo.sh
python -m opspilot.otel_benchmark.experiment
python -m opspilot.otel_benchmark.experiment_report artifacts/otel_demo_rca_v1/<run-id>
```

`DEEPSEEK_API_KEY` 从环境或本地 `.env` 读取，不写入实验工件。Runner 为两个 Adaptive 模式显式启用真实 Planner，使用当前 `RuntimeSettings` 配置的模型，temperature=0、max_tokens=500、一次 API 尝试。失败沿用既有 fallback，但不会标为有效 LLM 决策。

每次运行保存 `config.json`、`dataset.json`、`environment.json`、`source_manifest.json`、完整源码归档、配置哈希，以及 `pairs.json` 和各模式 `records.json`。OpsPilot commit 是当前 HEAD；未提交源码的准确版本由快照哈希标识，没有自动提交或覆盖用户工作区。每个配对周期开始前检查冻结文件，禁止逐 Case 调参。

`<scenario>/repeat-XX/modes/<mode>/` 包含独立 Alert、真实原始查询、Tool arguments/ToolResults、Evidence、候选报告、ActionHistory/GateDecision，以及 `llm/api-XX.json`、`planner_decisions.json`、token usage 和最终分数。Controller 的全工具测量开销另存，不计入任何模式的 RCA Tool calls。

安全停止后可以使用同一冻结版本恢复：

```powershell
python -m opspilot.otel_benchmark.experiment --resume artifacts/otel_demo_rca_v1/<run-id>
```

共享锁避免并发注入。仍活动的锁不能删除；异常结束需确认故障关闭及恢复。失败 attempt 原样保留，重试另建目录；正式统计只选择恢复完成的最新配对，不增加重复次数的分母。不能在修改冻结源码后继续同一个 run。

报告从机器工件计算 Fault Top1/Top3、Normal、Gate/budget/degraded、平均 Tool/Expert/rounds/LLM、P50/P95 latency 与真实 token usage。没有可靠单价配置时不估现金成本。错误分类基于实际工具、证据、行动与 Gate，条件共现不证明单一因果；无法确定时标 unknown。

Knowledge Tool 只作决策，不接 EvalRAG。五项标准必须同时核验：多次真实失败、遥测正常、实时 Evidence 无法区分、独立历史 incident/runbook/service docs 确有区分信息、不是简单 Tool/Evidence/Planner bug。没有核验的条件不当作满足；benchmark 答案和 flag 源码不能当知识库。

最终输出 `reports/otel_demo_rca_v1.md`、运行目录和 `artifacts/` 下的 failure_analysis、`artifacts/knowledge_tool_decision.md`、`artifacts/resume_evidence.md`。负结果也保留；这些数字只表示官方近真实微服务环境，不能写成企业生产数据或生产落地。

本轮三 repeats 的完成核查由 `scripts/otel_demo/complete_rca_release.py` 执行：确认真实矩阵、冻结文件、配对输入及 Agent 隔离，运行默认测试环境中的 pytest 与 Ruff，再根据实际工件更新 README 和计划。live 环境变量不会传入默认回归测试。`final_verification.json` 和 `independent_audit.json` 分别保存测试与独立核查。

完成后的 `refine_rca_failure_conditions.py` 只复核本轮已保存输入与冻结 Evidence 规则，补充 C/D/F 条件；不更改 Engine、门槛、排名、Ground Truth 或分数。原分析另存 `analysis_refinement/`，机器摘要为 `failure_condition_refinement.json`，唯一因果仍标 unknown。

Git 仅保存精简配置、哈希、逐次分数、汇总及报告；完整 raw telemetry、模型请求/响应、控制历史和源码归档保留在本地忽略目录，没有删除。目录策略见 `artifacts/otel_demo_rca_v1/README.md`；正式 release 的完整本地目录约 2.8 GiB，不能把精简 Git 工件当作全部原始数据。
