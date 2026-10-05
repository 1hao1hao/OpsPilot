# OpenTelemetry Demo 真实 RCA Benchmark v1

三个模式的正式真实 LLM Planner / L2 消融入口见 [EXPERIMENT.md](EXPERIMENT.md)。

OpsPilot 没有企业生产数据。本数据集在 **OpenTelemetry 官方 Astronomy Shop 近真实微服务环境中验证**，使用真实 Locust、feature-flag fault injection 和 Prometheus / Jaeger / OpenSearch 遥测。

`scenarios.yaml` 是控制器数据集，包含六个 Fault 和一个 Normal。固定 upstream commit `7ea09b865a9b9d411b59459b15133d84f8c2784a`、Demo 3.1.0、Collector 0.160.0；完整服务需要 core + full + observability，使用现有集成脚本。实际镜像身份保存在每次运行的 environment artifact。

| 场景 | Controller flag/variant | 初始 operational category | 告警所指服务 |
| --- | --- | --- | --- |
| Normal | 所有故障 off | NO_FAULT | frontend |
| compute | adHighCpu / on | RESOURCE_SATURATION | ad |
| payment-errors | paymentFailure / 100% | RPC_ERROR_RATE | checkout |
| dependency-unavailable | paymentUnreachable / on | RPC_ERROR_RATE（实测 ERROR、is_timeout=false；连接不可用 taxonomy gap） | checkout |
| messaging-backlog | kafkaQueueProblems / on | KAFKA_CONSUMER_LAG | checkout |
| database-latency | productCatalogLockContention / on | DB_SLOW_QUERY（锁竞争机制与 DB 延迟类别不等价） | product-catalog |
| memory-growth | emailMemoryLeak / 100x | RESOURCE_SATURATION；未观察到 OOM 不标 OOM_RESTART | email |

正式解释以实测报告和对应 dataset artifact 中的 mapping 为准。不能凭 flag 名证明 operational fault 已产生。
首次实测结果与失败分析见 [Benchmark 报告](../../../../reports/otel_demo_benchmark.md)，前置环境验收见 [live 报告](../../../../reports/otel_demo_live_smoke.md)。
smoke 的 1.0.0 先用 provisional RPC_TIMEOUT；实际 payment 客户端失败 span 的 `is_timeout=false`，因此 1.0.1 改为现有 RPC_ERROR_RATE 并记录 gap。旧 run 保留原始 mapping 和分数，不回写成新答案。

## 本机执行

Windows PowerShell，项目根目录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
. ./scripts/otel_demo/local_live_env.ps1
bash scripts/otel_demo/start_demo.sh
python -m opspilot.otel_benchmark.runner --profile smoke
# 仅初步单次正式流程；Normal 始终真实重复三次
python -m opspilot.otel_benchmark.runner --profile release --repeat 1
# 完整正式重复：六个故障各三次 + Normal 三次，总计 21 次
python -m opspilot.otel_benchmark.runner --profile release
```

可用 `--scenario <scenario-id>` 选择子集；不选择时执行七个场景。时间参数、负载、重复次数在 YAML 中配置，实际 overrides 和完整 dataset 均写入工件。smoke 只验证流程，单次 release 只能给初步分数，不能代表正式重复准确率。

Locust 保持 5 用户、spawn rate 1；记录配置和各阶段实际 stats、请求计数增量。Runner 使用自带 Locust `/swarm` 与 `/stats/requests`，没有自造 HTTP 请求循环。

每场景 reset → baseline/真实查询 → enable one fault → stabilization/observation → symptom-only Alert → 现有 OpsPilotWorkflow → 保存报告和分数 → finally reset → cooldown/真实恢复查询。Memory 场景结束后显式重启 email 清除保留的邮件和堆内存，记录人工恢复，不将它计为 OOM。恢复 health/queries 和 CPU/memory/lag 差值分别保存；HTTP 健康不等于症状完全恢复。
release 在 scoped baseline 没有 trace 时进行有上限的等待；故障关闭后，对实际增加的资源/错误率/lag/延迟观测做恢复检查，未恢复就停止，避免污染下一 case。Memory 注入达到 85% 容器内存上限时中止并恢复，不冒充 OOM 成功。阈值和等待上限是 Controller 的实验安全/有效性检查，绝不进入 Agent 的预测或置信度。

## Ground Truth 隔离

`fault_control`、`ground_truth` 和 scenario ID 只在 Controller / scorer 使用。Alert 由独立 `AlertTemplate` allowlist 构造，ID 为随机 UUID，无 signals、labels、场景名或答案。现有 RCA、Planner/Fallback、Ranker、Gate 和 Runtime 均不改变。

真实日志可能直接包含 flag 名。Runner 在工具返回、Evidence 构建之前过滤控制面日志/feature-flag spans，移除控制元数据，脱敏邮箱；Alert、工具参数、ToolResult、Evidence 和报告均执行泄漏断言。所有正常 RPC、错误率、CPU/memory 等观测值保持实测值。原始响应保存在 controller 的 `telemetry/raw/`，不传入 RCA；隔离审计记录过滤数量。`changes.query` 沿用无部署事实源时的空结果，测试保证它不能访问 flagd。

Controller 使用固定 upstream 已存在的 [flagd-ui Storage](https://github.com/open-telemetry/opentelemetry-demo/blob/7ea09b865a9b9d411b59459b15133d84f8c2784a/src/flagd-ui/lib/flagd_ui/storage.ex) `/api/read`、`/api/write`：临时文件写入后同文件系统原子 rename。只挂载独立运行副本，不修改 upstream tracked source。写入后通过 [flagd Connect evaluation](https://flagd.dev/reference/cheat-sheet/) 的实际 variant 确认 reload，测试覆盖异步延迟。保存 before/requested/confirmed 状态，开始、结束、异常时均 reset；独占 lock 避免同一 Demo 两个 Runner 同时改 flags。强行终止进程/宿主掉电无法保证 finally，需检查状态并执行 reset，不能直接移除仍活动的锁。

## 工件与分数

`artifacts/otel_demo_benchmark/<run-id>/<scenario-id>/repeat-XX/`：

- `scenario.json`, `environment.json`, `alert.json`, `control/`, `load/`, `lifecycle/`。
- `baseline/`、`telemetry/`、`recovery/`：完整工具结果、metrics_normalized、logs、traces；`telemetry/prometheus_queries.json`, `raw_manifest.json`, `raw/`。
- `rca/report.json`, `events.json`, `tool_results.json`, `tool_arguments.json`, `evidence.json`。
- `benchmark/ground_truth.json`, `score.json`, `observed_delta.json`, `isolation_audit.json`, `trial_status.json`。

运行根目录保存 dataset/hash、运行版本的 source archive/hash、镜像 IDs、全部 trials 和按场景/总体 summary。评分只比较现有候选类别，root_service 另存，**不**宣称定位到准确服务。Fault Top-1 / Top-3 与 Normal NO_FAULT 分开计算；同时显示 Gate、budget exhausted、degraded、tool/expert 调用、轮次、延迟。后端失败和故障未产生可观测差异分别列出，不能伪造为空或“正确”。summary 另列 fault 已观察确认的子集分数，避免把未确认效果的 case 当成有效故障诊断。

`fixtures/` 保存真实采集、仅脱敏/移除控制面内容的 normalized observations，附 source、upstream commit、captured_at、scenario、原始工件路径和生命周期/故障观察状态。用于离线 regression，不手工改成理想答案。没有生产数据、LLM 大规模实验、EvalRAG、Knowledge Tool 或每故障专用诊断规则。
开发期 preflight fixture 若未完成生命周期会明确标记 `lifecycle_complete=false`；正式 fixture 以对应 run 子目录中的 metadata 为准。上游 Locust 还尝试可选 agent 的 `/prompt`，当前 core/full/observability 未部署可选 AI layer，存在背景请求失败；这在运行工件和报告中列为 gap，Normal 的预期仅针对 frontend 的无注入服务状态，不宣称整个系统无任何背景错误。
