继续处理 OpsPilot：

Repository:
https://github.com/1hao1hao/OpsPilot

前一个任务已经完成 OpenTelemetry Astronomy Shop telemetry integration 的代码实现和离线测试，但由于当时执行环境没有 Docker，以下内容被 SKIPPED：

- Astronomy Shop 真实部署
- live telemetry smoke
- Prometheus metric discovery

本任务只做“真实环境验收和必要的小修复”。

禁止扩大范围。

==================================================
一、不要处理旧 Benchmark 的 6/21 fallback 问题
==================================================

当前已知：

简化 deterministic fallback：
dev fault Hit@1 = 6/21

完整观测：
dev fault Hit@1 = 21/21

这是既有旧 Benchmark 在无 LLM、L1 线索不足时的覆盖率限制。

本任务：

- 不增加关键词 fallback
- 不恢复 alert_type → domain 猜测
- 不修改 Planner 架构
- 不为了恢复 21/21 调规则
- 不修改 Evidence Gate / Ranker

把这个限制原样保留。

本任务唯一目标：

证明真实 OpenTelemetry Astronomy Shop → OpsPilot 数据链跑通。

==================================================
二、先检查前一阶段实际实现
==================================================

不要假设文件路径。

检查当前仓库实际已经实现的：

- Astronomy Shop bootstrap/start/stop
- Prometheus client
- Jaeger client
- OpenSearch client
- live ObservationProvider
- settings / env
- metric discovery
- integration smoke

如果前一个任务实际文件名与原计划不同，沿用当前实现。

禁止重新实现第二套 integration。

==================================================
三、运行环境检查
==================================================

首先执行并记录：

docker --version
docker compose version

确认当前用户有权限：

docker info

如果 Docker 不可用：

停止真实实验并明确报告原因。

不要用 fixture 结果冒充 live smoke。

==================================================
四、启动固定版本 Astronomy Shop
==================================================

使用前一阶段锁定的：

OpenTelemetry Demo commit / tag。

启动：

core
+
full
+
observability

目标至少包括：

- frontend
- load-generator
- flagd
- otel-collector
- Prometheus
- Jaeger
- OpenSearch
- PostgreSQL
- Kafka
- Valkey

使用项目已经实现的 bootstrap/start 脚本。

不要修改 upstream tracked source，除非 integration 原设计明确需要 override。

等待 health checks 稳定。

记录：

docker compose ps

到 artifact。

==================================================
五、验证正常流量
==================================================

确认 Astronomy Shop 自带 load-generator 正在产生请求。

检查：

frontend 可访问
Prometheus 有持续变化的数据
Jaeger 有新的 traces
OpenSearch 有新的 logs

不要只验证 HTTP 200。

必须证明：

三个 telemetry backend 都有 Astronomy Shop 实际数据。

==================================================
六、执行 Prometheus Metric Discovery
==================================================

运行前一阶段实现的 metric discovery。

真实查询当前 Prometheus。

输出：

- metric names
- relevant labels
- service.name 等真实 label
- OpsPilot 当前 metrics.query 使用的实际 PromQL

重点确认：

1. request rate
2. error rate
3. latency
4. CPU
5. memory
6. PostgreSQL
7. Kafka
8. Valkey/Redis-compatible

哪些实际存在就记录哪些。

不存在的：

不要伪造。

如果当前 adapter 的 metric name / label 与真实环境不一致：

根据真实 discovery 做最小修复。

然后重新测试。

==================================================
七、逐个验证 OpsPilot Tool
==================================================

针对真实 Astronomy Shop 正常状态，执行：

metrics.query
logs.query
traces.query
topology.query（如果已有真实实现）

以及：

db.*
redis.*
kafka.lag
rpc.metrics

每个 Tool 保存：

raw backend response reference
normalized observations
ToolResult

要求区分：

A. 查询成功且有数据
B. 查询成功但真实环境没有该观测
C. backend / query error

不能把 C 当成空数据。

==================================================
八、至少跑一个真实 Fault Smoke
==================================================

为了证明数据链不只是 Normal Case 工作：

从 Astronomy Shop 当前存在的 feature flag 中选择一个最容易观察的故障。

优先：

adHighCpu

流程：

所有 fault off
↓
运行正常流量
↓
采 normal baseline
↓
开启 adHighCpu
↓
等待 telemetry 稳定
↓
再次运行 metrics.query / traces.query
↓
关闭 fault

只需要验证：

故障前后真实 telemetry 有可观察差异。

这还不是正式 Benchmark。

不要：

- 调整 Ranker 保证答对
- 构建完整 6 Fault 数据集
- 宣称 RCA Accuracy

如果 adHighCpu 无法稳定产生观测差异：

记录原因，并尝试一个简单备选，例如 paymentFailure。

最多验证 1 个成功 fault smoke。

==================================================
九、执行一次真实 OpsPilot RCA
==================================================

基于刚才的真实 Fault：

构造 symptom-only Alert。

Alert 不能出现：

adHighCpu
feature flag 名
ground truth

让当前 OpsPilot 正常运行一次。

保存：

Alert
ToolResults
Evidence
Top-K
Gate
ActionHistory
DiagnosisReport

这里不要求结果一定正确。

目标只是证明：

Astronomy Shop
→ telemetry
→ Tool
→ Evidence
→ RCA Engine

完整贯通。

如果没有启用真实 LLM：

明确记录 planner 使用 fallback。

不要把它作为正式 Adaptive 准确率。

==================================================
十、输出验收报告
==================================================

生成：

reports/otel_demo_live_smoke.md

至少包括：

1. OpenTelemetry Demo 固定版本。
2. Docker / Compose 环境。
3. 启动的服务。
4. Prometheus / Jaeger / OpenSearch health。
5. 实际发现的主要 metrics。
6. 每个 OpsPilot Tool live smoke 状态：
   PASS / EMPTY_BY_DESIGN / FAIL
7. Fault smoke 使用哪个 flag。
8. 故障前后的真实 telemetry 差异。
9. 一次真实 RCA 的完整结果。
10. 所有 artifact 路径。
11. telemetry gap。
12. 当前仍未验证的内容。

最后明确给出：

LIVE_INTEGRATION = PASS / FAIL

只有满足：

Prometheus live query 成功
+
Jaeger live query 成功
+
OpenSearch live query 成功
+
至少一个真实 Fault 产生可观测差异
+
OpsPilot 完整 RCA 链执行成功

才能标：

LIVE_INTEGRATION = PASS

==================================================
十一、本任务不要做
==================================================

不要：

- 开始 6 Fault Benchmark
- 跑正式 Accuracy
- 大规模 LLM 调用
- 接 EvalRAG
- 修改 fallback 恢复 21/21
- 修改 RCA 主架构
- 新增新的 Agent
- 修改 Runtime
- 为单个 Fault 编特殊判断

这里只完成真实环境验收。

==================================================
本次执行记录（2026-10-04，Asia/Shanghai）
==================================================

状态：环境检查已执行，真实实验未执行，LIVE_INTEGRATION = FAIL。
`docker --version`、`docker compose version`、`docker info` 均返回退出码127，Docker CLI不可用。
遵照第三节停止真实实验；没有启动服务、采集metrics/traces/logs、切换故障或运行真实RCA。
报告：reports/otel_demo_live_smoke.md。
原始检查与结构化状态：artifacts/otel_demo/live_acceptance/20261004T124738+0800/。
未将fixtures或离线测试冒充live结果，未处理旧Benchmark覆盖率问题。

本地 Windows 后续进展（2026-10-04，Asia/Shanghai）：迁移检查已完成，分支/HEAD 与原记录一致，git fsck 退出码0；原 Linux .venv 已改名备份，本机 Python 3.12 环境重建并安装 .[dev]，全套测试216 passed、4 skipped。仅修复部署脚本及测试的 Windows 兼容性。已安装 Git、Docker Desktop 和 Compose，启用 WSL/VirtualMachinePlatform；功能启用明确要求重启。docker info 与实际 hello-world 容器均未通过，WSL 包/daemon 需重启后复核。按第三节停止真实实验；LIVE_INTEGRATION = FAIL，正常流量、Fault smoke、完整 live RCA 仍未执行。详见 reports/otel_demo_live_smoke.md 的本地迁移节，工件 artifacts/otel_demo/local_migration/。未修改 Planner/Fallback、Evidence Gate、Ranker 或 Runtime。

最新本地验收（2026-10-04T21:51:58.955770+08:00）：LIVE_INTEGRATION = PASS。Docker info、hello-world、固定 Demo 28 服务及后端健康均通过；连续真实 metrics/traces/logs 数据增长，13 工具正常 smoke 无 FAIL；adHighCpu JVM CPU 从 0.000304528 增至 0.139741598，关闭后恢复 0.000106227。一次 symptom-only RCA completed、7 条 Evidence；未启用 LLM，使用原 fallback；Top-1 rpc_timeout、Top-2 resource_saturation，Gate insufficient 后预算耗尽，如实记录，不声称准确率。全套测试 221 passed / 4 skipped，单独 live 测试 1 passed。upstream clean，故障最终全部 off。仅修复部署 Windows 兼容、资源 override 和实测 Prometheus/Jaeger 适配；未改 Planner/Fallback、Gate、Ranker、Runtime，未处理旧 Benchmark。详见 reports/otel_demo_live_smoke.md 最新节及 artifacts/otel_demo/live_acceptance/local_windows/acceptance_summary.json。
