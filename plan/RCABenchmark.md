task2
继续改造：

Repository:
https://github.com/1hao1hao/OpsPilot

前提：

上一阶段已经完成 OpenTelemetry Astronomy Shop integration。

OpsPilot 能从真实：

- Prometheus
- Jaeger
- OpenSearch

读取 telemetry。

不要重新实现 telemetry adapter。

==================================================
一、本任务目标
==================================================

构建第一版真实 RCA Benchmark：

OpenTelemetry Astronomy Shop
+
真实 Load Generator
+
真实 Feature Flag Fault Injection
+
OpsPilot RCA

固定：

6 个 Fault
+
1 个 Normal Case

生成版本化、可复现 Benchmark。

==================================================
二、最重要原则：Ground Truth 隔离
==================================================

必须严格分离：

Benchmark Controller

和

OpsPilot RCA Engine。

Benchmark Controller 可以知道：

fault_flag = paymentFailure
expected_root_cause = rpc_error_rate

但是传给 OpsPilot 的 Alert：

不能包含：

paymentFailure
feature flag name
expected root cause
fault metadata
scenario name
答案描述

OpsPilot 只能看到：

- symptom Alert
- Metrics
- Logs
- Traces
- Domain telemetry

禁止 Ground Truth Leakage。

增加自动测试检查：

Alert JSON / ToolResult

中不存在：

fault flag name
expected_root_cause_type

==================================================
三、第一版 Benchmark 场景
==================================================

使用当前 OpenTelemetry Demo 实际存在的 Feature Flags。

固定六个：

1.

flag:
adHighCpu = on

目标 operational RCA category：

RESOURCE_SATURATION

注意：
不要把 Alert 直接写成“ad CPU high because high CPU fault”。

Alert 应描述用户可观测 symptom。

根据真实实验选择：

frontend / ad latency
或者服务级 resource alert。

--------------------------------------------------

2.

paymentFailure

使用一个明确比例，例如：

100%
或经过 smoke 后选择足够稳定且可诊断的比例。

expected:

RPC_ERROR_RATE

Alert：

checkout / frontend error-rate symptom。

--------------------------------------------------

3.

paymentUnreachable = on

expected:

RPC_TIMEOUT

或当前 taxonomy 中最接近、且真实 telemetry 能支持的 downstream-unavailable 类型。

不要为了 benchmark 随便增加类型。

如果真实表现更接近明确 connection error 而当前 taxonomy 无法表达：

记录 taxonomy gap。

可以增加一个必要的新 RootCauseType，
但必须说明理由，且最多做最小扩展。

--------------------------------------------------

4.

kafkaQueueProblems = on

expected:

KAFKA_CONSUMER_LAG

必须使用：

compose.full.yaml

确保 Kafka / consumers 存在。

--------------------------------------------------

5.

productCatalogLockContention = on

这是 DB Case。

当前 RootCauseType 没有 DB_LOCK_CONTENTION。

请先实际观察 telemetry。

如果真实数据能够区分 lock contention：

允许增加：

DB_LOCK_CONTENTION

并加入对应 Evidence 逻辑。

这是“真实环境暴露了现有 taxonomy gap”，属于合理扩展。

如果真实 telemetry 无法可靠区分：

不要硬加。

可以暂时映射到：

DB_SLOW_QUERY

但必须在 Benchmark metadata 和报告里写清：

fault mechanism = lock contention
RCA category = database latency/bottleneck

禁止把两者写成完全等价。

--------------------------------------------------

6.

emailMemoryLeak

选一个不会快速把整个环境打崩、
但能产生稳定 memory trend 的 variant，
例如先测试 10x / 100x。

expected operational category：

RESOURCE_SATURATION

如果最终真的稳定产生 OOM restart：

才可以改成：

OOM_RESTART

不要因为 flag 名叫 MemoryLeak 就让 Agent 直接预测“memory leak”。

Agent 只能根据 telemetry 推断。

==================================================
四、Normal Case
==================================================

全部 Benchmark Fault Flags：

off

保持正常负载。

expected：

NO_FAULT

至少重复多次。

这个 Case 很重要：

不能只有“有故障一定猜一个答案”。

需要验证系统在正常环境下不会强行诊断故障。

==================================================
五、Scenario Schema
==================================================

建立新的真实 Benchmark schema。

例如：

benchmarks/datasets/otel_demo/v1/scenarios.yaml

每个 scenario 可以保存：

scenario_id
upstream_version
fault_control:
    flag
    variant
ground_truth:
    root_cause_type
    root_service
alert_template
warmup_seconds
fault_stabilization_seconds
observation_window_seconds
cooldown_seconds
repeat
tags

注意：

fault_control 和 ground_truth：

只能被 Benchmark Runner 使用。

绝对不能传给 RCA Engine。

生成给 OpsPilot 的 AlertEvent：

单独构造。

==================================================
六、Fault Controller
==================================================

实现：

OpenTelemetryDemoFaultController

职责只有：

set_fault(flag, variant)
reset_all_faults()
read_current_flags()

可以利用 upstream：

src/flagd/demo.flagd.json

实际机制。

但是：

1. 不永久修改 upstream tracked file。
2. 每个 scenario 开始前 reset。
3. scenario 结束后 reset。
4. 程序异常退出也尽最大可能恢复。
5. 保存修改前/后的 flag state artifact。
6. 原子更新文件，避免半写 JSON。
7. 等待 flagd reload 生效后再跑实验。

如果 upstream 当前有更稳定的官方 flag API：

可以用官方方式。

但必须先确认，不要凭空假设 endpoint。

==================================================
七、真实 Load Generator
==================================================

使用 Astronomy Shop 自带 Locust。

不要自己造 HTTP loop 替代它。

Benchmark Runner 应确认：

load-generator 正在持续产生流量。

记录：

- user count
- spawn rate / load config
- experiment duration

第一版保持固定负载。

不要 Fault A 一个负载、Fault B 另一个负载。

否则无法比较。

==================================================
八、实验生命周期
==================================================

每个 Fault：

RESET ALL FAULTS
↓
等待 normal baseline
↓
确认 telemetry 正常
↓
记录 baseline window
↓
ENABLE one fault
↓
等待 fault stabilization
↓
构造 symptom-only Alert
↓
OpsPilot RCA
↓
保存所有 artifact
↓
DISABLE fault
↓
cooldown
↓
确认恢复

Normal：
RESET ALL FAULTS
↓
baseline / load
↓
Alert
↓
RCA

所有时间参数：
可配置。
建议不要硬写进代码。
提供：
smoke profile
→ 时间短，只验证流程
release profile
→ 时间足够，正式 Benchmark
==================================================
九、Benchmark 不能只保存最后答案
每次运行保存：
artifacts/otel_demo_benchmark/<run-id>/
至少：
scenario.json
environment.json
alert.json
telemetry/
    prometheus_queries.json
    metrics_normalized.json
    traces.json
    logs.json
rca/
    report.json
    events.json / investigation trace
benchmark/
    ground_truth.json
    score.json
必须能复查：
“为什么这一条被判对/错”。
==================================================
十、Benchmark 指标
第一阶段至少计算：
Fault cases：
Top-1 RCA Accuracy
Top-3 RCA Accuracy
Normal：
NO_FAULT Accuracy
以及：
Gate Pass Rate
Budget Exhausted Rate
Degraded Run Rate
另外记录：
tool_calls
expert_calls
investigation_rounds
latency_ms
这个阶段先不要做复杂统计学。
==================================================
十一、重复运行
真实系统存在噪声。
正式 profile：
每个 Fault 至少支持 repeat >= 3。
不要只跑一次就写：
Accuracy = 100%。
结果报告必须同时显示：
按 scenario
+
总计。
例如：
paymentFailure:
2/3 Top1
而不是只有：
overall=85.7%。
==================================================
十二、建立真实 telemetry fixture
每个场景第一次成功跑通后：
可以保存一份真实采集到的脱敏 telemetry fixture，
用于以后：
offline regression tests。
但是 fixture 必须标明：
source = recorded from OTel Demo
upstream commit
captured_at
scenario
不能手工改造成“理想答案”。
==================================================
十三、测试 Ground Truth Leakage
强制新增测试：
benchmark scenario 有：
paymentFailure
但：
AlertEvent
Tool arguments
ToolResult
Evidence
不能包含字符串：
paymentFailure
或者 expected root cause 字段。
特别检查：
changes.query
不能通过 flagd 读取 fault flag。
==================================================
十四、本任务不要做
不要：
- 调 LLM 做完整大规模 Adaptive 实验
- 接 EvalRAG
- 加 Knowledge Tool
- 为每个 Fault 编专门规则
- 写：
  if scenario == paymentFailure → RPC_ERROR_RATE
- 把 Feature Flag 转成 Evidence
- 根据 Ground Truth 修改 confidence
- 为了达到高准确率调 benchmark 数据
Benchmark 和 Agent 必须严格隔离。
==================================================
十五、README
新增真实环境 Benchmark 说明：
明确：
OpsPilot 没有企业生产数据。
使用的是：
OpenTelemetry 官方 Astronomy Shop
近真实微服务测试环境。
说明：
- upstream version
- 真实 telemetry
- feature-flag fault injection
- load generator
- 6 faults + normal
不要写：
“生产环境验证”
“企业落地”
应该写：
“在 OpenTelemetry 官方近真实微服务环境中验证”。
==================================================
十六、完成后
运行 smoke profile。
如果环境允许，再至少完整跑：
每个 scenario 1 次。
不要在这一步为了节约时间伪造 repeat=3。
最终报告：
1. 7 个 Scenario。
2. Ground Truth mapping。
3. Alert 如何避免泄漏答案。
4. Fault Injection 如何实现。
5. 每个 scenario 实际观测到哪些 telemetry。
6. 哪些 scenario 已完整跑通。
7. 哪些失败，失败原因。
8. 单次初步 Top1 / Top3。
9. Normal Case。
10. Telemetry gap。
11. Taxonomy gap。
12. 下一阶段消融需要解决的问题。
所有数字必须来自 artifact。

==================================================
执行完成记录（2026-10-05，Asia/Shanghai）
==================================================

前置确认：plan/task1.5.md 最新验收为 LIVE_INTEGRATION=PASS，已完成本地迁移、Docker 实际容器、固定版本完整 Demo、真实流量/telemetry、Fault smoke 和一次完整 live RCA。历史 FAIL 记录保留，详见 reports/otel_demo_live_smoke.md。

本任务第一版实现及要求的真实实验已完成。新增 src/opspilot/otel_benchmark/，包含严格 schema、官方原子 API fault controller、flagd reload evaluation 确认、固定官方 Locust、生命周期 runner、Ground Truth 隔离、artifact scorer/report；数据集 benchmarks/datasets/otel_demo/v1/scenarios.yaml 固定 6 faults + Normal。release 默认支持故障各 repeat=3；本次真实执行 --repeat 1，故障各一次，Normal 实际三次，没有伪造三次正式重复。保存 13 份完整生命周期真实脱敏 fixture，离线回放不计作 live 实验。

Smoke 工件：artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/，dataset 1.0.0。9 次尝试，8 次完整并评分；payment-errors 因 scoped baseline 缺少 traces 在注入前失败，memory 短窗口未确认效果。Fault Top1=1/5、Top3=2/5，Normal=1/3；已确认故障子集为 Top1=1/4、Top3=2/4。开发期 preflight 序列化失败及主动中断单独保留，不并入分数。

Release 工件：artifacts/otel_demo_benchmark/20261004T151904Z-91254d/，dataset 1.0.1。9/9 完整生命周期，6/6 故障效果及恢复已确认。Fault Top1=3/6（50%）、Top3=4/6（66.7%）；Normal NO_FAULT=1/3。Gate pass=2/9，Budget exhausted=7/9，Degraded=0/9。分类评分不宣称精确 root_service 定位；不同 profile/version 不合并。

逐场景实测：CPU 比例 0.000148→0.139856，Top1 RESOURCE_SATURATION；支付错误率 0%→100%，Top1 RPC_ERROR_RATE；依赖不可用表现为 ERROR/连接/DNS 错误，Top1 RPC_ERROR_RATE，旧 smoke provisional RPC_TIMEOUT 不回写；Kafka lag 0→404→0，Top3 未命中 KAFKA_CONSUMER_LAG；数据库采集到 21 条慢查询，tp95/tp99 达 15000ms，恢复约 286/617ms，Top3 未命中 DB_SLOW_QUERY；email 内存比例 0.432344→0.533906，17 样本正增长，人工恢复重启后 0.434609，Top1 RPC_TIMEOUT、Top2 RESOURCE_SATURATION，未宣称 OOM。Normal 三次中两次误报 RPC_TIMEOUT，其中一次 Gate 放行；内存场景 Gate 也放行错误 Top1。

限制如实记录：Engine 未为 Kafka/DB 选择对应域工具；Engine 告警前历史窗口混入稳定阶段，与 Controller 的无故障 baseline 区分；连接不可用 taxonomy gap；锁竞争机制不能等同于 DB_SLOW_QUERY；缺失的部署事实/topology/部分领域指标；官方负载的可选 agent /prompt 服务未部署，存在背景请求失败。未增加诊断类别或每故障规则，未改 Planner/Fallback、Evidence Gate、Ranker、Runtime，未处理旧 Benchmark 6/21，没有 LLM 大规模实验、EvalRAG 或 Knowledge Tool。

最终 pytest：252 passed、4 skipped、1 既有依赖 deprecation warning，exit=0；Ruff exit=0。112 份 Alert/工具参数/ToolResult/Evidence/report JSON 通过更严格的 key/value 隔离复查。59 个非 Benchmark 源码文件与运行开始归档相同，既有工作区修改仍保留；upstream clean，28 个服务健康运行，所有故障 off。检查数据见 release 的 final/verification.json、final/test_results.json，测试日志 artifacts/otel_demo_benchmark_tests_final.txt。

正式逐场景报告、Ground Truth mapping、完整遥测/调用/延迟、失败原因和下一阶段消融问题：reports/otel_demo_benchmark.md。复现指南：benchmarks/datasets/otel_demo/v1/README.md。本记录的“完成”表示本版构建和真实初步验收完成，不表示诊断准确率达标或正式三次重复实验完成。
