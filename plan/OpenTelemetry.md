task1
你现在需要继续改造项目：

Repository:
https://github.com/1hao1hao/OpsPilot

上游真实微服务实验环境：
https://github.com/open-telemetry/opentelemetry-demo

这是 OpsPilot 重构后的下一阶段。

非常重要：
开始修改前，先重新完整检查 OpsPilot 当前 main 分支。
不要按照旧架构理解项目。

当前 OpsPilot 已经完成简化重构，真实核心链路应当接近：

Alert
→ Coordinator
→ L1 Tools
→ ToolResult
→ Evidence Builder / AnomalyDetector
→ Evidence Pool
→ Deterministic RootCause Ranking
→ Evidence Gate
→ Planner（证据不足）
→ Tool / Expert
→ 新 Evidence

Runtime：

API
→ PostgreSQL
→ Redis Queue
→ Worker
→ Checkpoint
→ Recovery

本任务禁止重新设计上述核心架构。

==================================================
一、任务目标
==================================================

把当前：

ObservationProvider
→ Mock HTTP / benchmark signals

扩展为：

OpenTelemetry Astronomy Shop
        ↓
Prometheus / Jaeger / OpenSearch
        ↓
Live Observation Provider
        ↓
现有 ToolRegistry
        ↓
ToolResult
        ↓
现有 Evidence / RCA 链

也就是说：

只替换 / 增加“真实数据源适配层”。

不要修改：

- Coordinator → Evidence → Ranker → Gate → Planner 主链
- RootCause Ranking 设计
- Evidence Gate
- Runtime
- PostgreSQL / Redis Runtime Queue
- Checkpoint
- ToolCall 幂等
- Planner Action 类型

==================================================
二、先确认 Astronomy Shop 当前真实结构
==================================================

不要凭印象实现。

先检查当前：

open-telemetry/opentelemetry-demo

重点：

- compose.yaml
- compose.full.yaml
- compose.observability.yaml
- .env
- src/flagd/demo.flagd.json
- src/otel-collector/
- src/prometheus/
- src/load-generator/
- Jaeger
- OpenSearch
- PostgreSQL
- Kafka
- Valkey

当前预期启动模式为：

docker compose \
  -f compose.yaml \
  -f compose.full.yaml \
  -f compose.observability.yaml \
  up -d

但请根据当前 upstream 实际内容确认。

当前已知 observability 数据路径是：

Metrics:
services / collector
→ Prometheus

Traces:
services
→ OTel Collector
→ Jaeger

Logs:
services
→ OTel Collector
→ OpenSearch

Kafka/PostgreSQL/Valkey：
OTel Collector receiver
→ Metrics
→ Prometheus

不要 fork 或复制整个 OpenTelemetry Demo 到 OpsPilot 仓库。

==================================================
三、固定 upstream 版本，保证 Benchmark 可复现
==================================================

不要让测试依赖：

open-telemetry/opentelemetry-demo@latest

实现类似：

integrations/opentelemetry_demo/version.json

至少保存：

- repository
- commit SHA / tag
- demo image version
- OpsPilot integration schema version

提供脚本，例如：

scripts/otel_demo/bootstrap_demo.sh

行为：

1. 如果本地没有 demo：
   clone 到 gitignored 的临时目录，例如：

   .external/opentelemetry-demo/

2. checkout 固定 commit。

3. 不提交整个 upstream repo。

4. 不修改用户已有其他 OpenTelemetry Demo clone。

5. 支持：

OTEL_DEMO_DIR=/path/to/opentelemetry-demo

让用户自行指定已有 clone。

==================================================
四、提供 Benchmark 专用部署入口
==================================================

不要把 Astronomy Shop 服务硬塞进 OpsPilot 主 docker-compose。

建议：

integrations/opentelemetry_demo/
    compose.override.yaml
    version.json
    README.md

scripts/otel_demo/
    bootstrap_demo.sh
    start_demo.sh
    stop_demo.sh
    healthcheck.py
    discover_metrics.py

允许通过 override 显式暴露 Benchmark 需要的端口：

Prometheus
Jaeger
OpenSearch
flagd / feature flag control
Locust（如需要）

但不要暴露无关端口。

启动完成后 healthcheck 至少验证：

- frontend 可访问
- load-generator 可运行
- Prometheus API 正常
- Jaeger Query API 正常
- OpenSearch 正常
- flagd 正常
- Kafka（full mode）
- PostgreSQL

==================================================
五、实现真实 Observation Provider
==================================================

不要把 OpenTelemetry 逻辑直接写进 ToolRegistry。

保持：

ToolRegistry
↓
ObservationProvider interface

增加类似：

OpenTelemetryDemoProvider

或：

LiveTelemetryProvider

职责：

根据 OpsPilot Tool 请求，从真实 telemetry backend 返回当前 Tool 已经认识的 observations。

建议拆成：

PrometheusClient
JaegerClient
OpenSearchClient

然后：

OpenTelemetryDemoProvider

组合它们。

保持现有：

ObservationOutput(
    observations={...}
)

契约。

==================================================
六、Metrics Tool
==================================================

现有：

metrics.query

应该真实查询 Prometheus。

至少需要能够得到：

- request rate / QPS
- error rate
- latency，例如 P95/P99
- CPU
- memory

注意：

绝对不要凭空猜 Prometheus metric name。

Astronomy Shop / OTel semantic convention 的 metric name 可能随着版本变化。

先实现：

scripts/otel_demo/discover_metrics.py

通过 Prometheus API：

- 查询 metric names
- 查询 labels
- 按 service.name 搜索相关 metric
- 输出真实发现结果到 artifact

例如：

artifacts/otel_demo/discovery/metrics.json

然后根据实际发现出的 metric name 和 label 建映射。

允许兼容少量 known aliases，
但禁止用不存在的 metric 名假装测试通过。

必须支持时间窗口：

alert timestamp 前后一段区间。

不要永远只 query 当前 instant value。

这样 Historical Baseline / IQR / Rolling Volatility 才有真实时间序列。

==================================================
七、Traces Tool
==================================================

现有：

traces.query

接 Jaeger Query API。

至少能够：

按：

- service
- 时间窗口
- error
- latency

查询 Trace / Span。

转换成当前 detect_span_anomalies 能消费的结构。

保留：

trace_id
span_id
service
operation
duration
status/error
parent/path

这样 OpsPilot 可以真正判断：

frontend
→ checkout
→ payment

等依赖链异常。

不要让 RCA Engine 直接理解 Jaeger 原始 JSON。

Jaeger JSON → adapter → 当前 Tool observation schema。

==================================================
八、Logs Tool
==================================================

现有：

logs.query

查询 OpenSearch：

otel-logs-* / 当前 upstream 实际 index

按：

- service
- 时间窗口

过滤。

返回精简后的：

logs/messages

每条至少包含：

timestamp
service
severity
message

不要把几千行原始日志全文塞进 ToolResult。

限制结果数量，并优先：

ERROR / WARN / exception / timeout 等高价值记录。

==================================================
九、领域 Tool
==================================================

继续复用当前 Tool 名称，不要重新造十几个 Tool。

### Kafka

kafka.lag

从 Prometheus 中读取 OTel Kafka receiver 输出的真实 lag。

用于：

kafkaQueueProblems。

### DB

Astronomy Shop 当前是 PostgreSQL，不是原 Mock MySQL。

让：

db.slowlog
db.connections
db.replication

根据实际可观测能力适配。

注意：

Astronomy Shop 是单 PostgreSQL 实例。

如果没有 replication：

db.replication

应该返回“没有对应观测/空 observations”，
而不是制造 replication lag。

productCatalogLockContention 重点可以来自：

- PostgreSQL receiver metrics
- DB-related trace latency
- deadlock / lock-related telemetry（只有实际存在才使用）

禁止制造不存在的数据。

### Redis

实际是 Valkey。

现有：

redis.memory
redis.hotkeys

可以继续使用 Redis 这个 OpsPilot 领域抽象名称，
但底层数据来自 OTel Redis/Valkey receiver。

README 说明：

Astronomy Shop uses Valkey as the Redis-compatible cache backend.

==================================================
十、RPC Tool
==================================================

rpc.metrics

优先从：

- Prometheus span metrics
- 或 traces

得到：

timeout_rate
error_rate
latency_ms
baseline_latency_ms
call_volume

保持现有 Evidence Builder 接口。

==================================================
十一、changes.query 特别注意答案泄漏
==================================================

Benchmark Harness 后续会通过：

flagd feature flag

开启：

adHighCpu
paymentFailure
...

但：

changes.query 绝对不能直接读取：

“当前打开了哪个 fault flag”。

否则 RCA 系统是在偷看 Ground Truth。

Benchmark Harness：

知道 fault flag。

OpsPilot RCA：

不知道 fault flag。

这是强制要求。

目前真实环境没有部署变更数据源时：

changes.query

可以返回空 observations。

不要为了让结果好看，把 feature flag 当 deployment change Evidence。

==================================================
十二、Alert 也不能泄漏答案
==================================================

本任务先建立接口要求。

后续 Benchmark 创建 Alert 时：

允许：

“checkout error rate elevated”

不允许：

“payment service failure caused checkout errors”

允许：

“frontend latency elevated”

不允许：

“adHighCpu fault active”

Alert：

只描述 symptom。

Ground Truth：

只能存在 Benchmark Metadata 中。

==================================================
十三、配置
==================================================

不要在代码里硬编码：

localhost:9090
localhost:16686
localhost:9200

增加环境变量 / settings，例如：

OPSPILOT_OBSERVATION_BACKEND=mock|otel_demo

OPSPILOT_PROMETHEUS_URL
OPSPILOT_JAEGER_URL
OPSPILOT_OPENSEARCH_URL

默认仍然允许 Mock：

保证现有 unit tests 快速运行。

只有显式：

OPSPILOT_OBSERVATION_BACKEND=otel_demo

才走真实环境。

==================================================
十四、失败处理
==================================================

真实 Backend 不可达时：

不要返回假的空正常数据。

应该 ToolResult ERROR，例如：

prometheus_unavailable
jaeger_unavailable
opensearch_unavailable

这样最终：

degraded=true

可以真实体现数据源故障。

真正查询成功但没有记录：

才返回空 observations。

==================================================
十五、测试
==================================================

分两层。

A. Unit tests

使用固定的真实格式 fixture：

Prometheus API response
Jaeger response
OpenSearch response

验证：

真实 backend JSON
→ adapter
→ ObservationOutput
→ Evidence

不能要求 unit test 启动整个 Astronomy Shop。

B. Integration smoke

如果 Docker 环境支持：

启动固定版本 Astronomy Shop。

验证：

metrics.query
logs.query
traces.query
kafka.lag
db.*
redis.*

能够查询真实服务。

如果执行环境不支持 Docker / 资源不足：

不要伪造“集成测试成功”。

清楚报告：

SKIPPED + 原因。

==================================================
十六、本任务不要做
==================================================

不要：

- 建完整 Benchmark
- 跑 6 Fault 实验
- 跑 LLM 消融
- 接 EvalRAG
- 新增 Knowledge Tool
- 修改 RCA Ranking
- 为了 Astronomy Shop 改掉 Evidence Gate
- 调参让特定 Fault 强行命中
- 增加大量新的 RootCauseType
- 重写 Runtime
- 重构已经完成的核心 Agent

本任务只解决：

“OpsPilot 能真实观察 Astronomy Shop。”

==================================================
十七、完成后输出
==================================================

最终报告：

1. upstream 固定到哪个 commit/tag。
2. Astronomy Shop 怎么启动。
3. 哪些 backend URL 被使用。
4. 每个 OpsPilot Tool 对应哪个真实数据源。
5. 实际发现了哪些 Prometheus metrics。
6. traces 如何转换。
7. logs 如何转换。
8. 哪些 Domain Tool 在 Astronomy Shop 上无真实数据，因此返回空。
9. Unit test 结果。
10. 如果运行了真实 Docker integration，给出真实 smoke 结果。
11. 还有哪些 telemetry gap。

不要声称没有真正跑过的实验结果。
==================================================
执行记录
==================================================

已实现：固定 upstream 版本、独立 Compose override 与 bootstrap/start/stop、healthcheck/discovery、Prometheus/Jaeger/OpenSearch 适配、现有 ToolRegistry 的 backend 选择、时间窗口和真实指标发现、异常码、无 feature-flag 答案访问、fixtures 单测及 opt-in live smoke。

交付说明：`integrations/opentelemetry_demo/README.md`。
验证记录：`artifacts/otel_demo/validation.json`、`artifacts/otel_demo/tests.txt`。
全量测试：204 passed / 4 skipped；Ruff 和脚本语法检查通过。
真实 Docker deployment / live smoke / Prometheus discovery：SKIPPED，当前执行环境没有 Docker。未声称实际发现指标或真实集成成功。

2026-10-04 复查补齐：
- RPC 按目标服务、client span、真实开始时间分离当前/历史窗口，去重；gRPC deadline exceeded / HTTP 504 在协议状态完整时产生 timeout_rate。
- Prometheus 支持带点标签的合法引用，全部 matcher 必须匹配同一真实 series；CPU 多核百分比转为有界利用率。
- live 模式默认工具预算30秒，保留显式配置；畸形 backend 数据归一化错误码，不返回空正常结果。
- 相对 clone 路径规范化；固定版本/脏工作区禁止启动，但停止/检查允许 flagd 变更且不 clone。
- live smoke 保存真实 discovery 和逐字段 telemetry_gaps；要求 Kafka/PostgreSQL/Valkey receiver 基础字段存在。
- 本次全量216 passed / 4 skipped / 1 warning；其中30个 adapter测试、4个部署脚本测试。Ruff、Shell syntax通过。
- Docker仍不可用，实际部署、live smoke、Prometheus discovery继续SKIPPED；不存在已采集metric清单。脚本测试使用假的Docker CLI，不算真实部署验证。
- 完整工具映射、URL、启动方式、数据转换和观测缺口见 integrations/opentelemetry_demo/README.md，验证见 artifacts/otel_demo/validation.json。
