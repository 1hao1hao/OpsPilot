# Astronomy Shop 数据源集成

只增加 ObservationProvider 数据适配和配置选择；Coordinator、Evidence、Ranker、Gate、Planner、Runtime 持久化与队列不变。默认仍使用 Mock，真实 Provider 忽略 `Alert.signals`、fault labels，且从不访问 flagd。

## 固定版本与核查

- OpsPilot 基线：`fcc74f5`，与当时远程 main 一致。
- [上游源码](https://github.com/open-telemetry/opentelemetry-demo/tree/7ea09b865a9b9d411b59459b15133d84f8c2784a)：`7ea09b865a9b9d411b59459b15133d84f8c2784a`。
- `version.json` 固定 demo 镜像 `3.1.0`、Collector `0.160.0`、集成 schema `1.0`；启动覆盖上游 `DEMO_VERSION=latest`，不使用浮动 demo 标签。
- 已核查 compose.yaml / full / observability、.env、flagd 配置、Collector 三层配置、Prometheus、Jaeger、Grafana 日志字段和 load-generator。
- 上游结构：OTLP traces → Collector → Jaeger；metrics → Collector → Prometheus OTLP receiver；logs → OpenSearch `otel-logs-*`。Full layer 加入 Kafka receiver；PostgreSQL 和 Valkey receiver 在基础 Collector 配置中。
- Astronomy Shop uses Valkey as the Redis-compatible cache backend.

## 启动与停止

需要 Docker Compose **2.24.4+**（`!override`），以及运行完整 upstream demo 所需资源和镜像网络访问。OpsPilot 本身仍按根 README 启动，不把 demo 合入主 Compose。

```bash
pip install -e '.[dev]'
scripts/otel_demo/bootstrap_demo.sh
scripts/otel_demo/start_demo.sh
# 停止 demo，不删除持久化卷
scripts/otel_demo/stop_demo.sh
```

默认 clone 到被 gitignore 的 `.external/opentelemetry-demo`，checkout 固定 SHA。指定已有 clone：

```bash
export OTEL_DEMO_DIR=/path/to/opentelemetry-demo
scripts/otel_demo/start_demo.sh
```

显式 clone 必须已处于固定 commit 且 tracked 文件干净；脚本只验证，不切换、不 reset、不修改用户 clone。当前上游使用固定 container_name，不能与另一套 Astronomy Shop 同时运行。镜像固定标签仍可能被上游重写；严格镜像级复现还需部署时记录镜像 digest。2026-10-04 Windows 实机已完成镜像下载、digest 校验、28 服务部署、故障 smoke 和一次 RCA，详见 [验收报告](../../reports/otel_demo_live_smoke.md)。

本机 Windows 使用已重建的 `.venv`、Git Bash 和 Docker Desktop Linux engine：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
. ./scripts/otel_demo/local_live_env.ps1
bash scripts/otel_demo/start_demo.sh
# 当前进程选择真实后端，禁用 LLM
$env:OPSPILOT_OTEL_LIVE_TEST = '1'
python -m pytest tests/integration/test_otel_demo_live.py -q
```

`local_live_env.ps1` 提供本机 checkout 资源配置及独立的 `.external/opspilot-flags` 运行目录；运行目录来自 upstream `src/flagd` 的副本。`OTEL_DEMO_FLAGD_DIR` 可指定其他副本，避免 UI 控制修改 upstream tracked 文件。`live_acceptance.py` 通过现有 Provider/Tools/RCA 捕获真实响应；仅其 fault 控制模式访问 flagd，ObservationProvider 不访问控制面。Jaeger 2.21.0 使用 v3 搜索；仅旧搜索明确 404 时进行接口适配，不将未知后端错误当成空数据。

启动实际使用 `compose.yaml + compose.full.yaml + compose.observability.yaml + 本集成 override`，禁止 build fallback。override 清除上游随机端口映射，仅绑定回环地址：

| 服务 | 默认 URL / 端口 |
| --- | --- |
| Frontend | http://127.0.0.1:18080 |
| Prometheus | http://127.0.0.1:19090 |
| Jaeger Query | http://127.0.0.1:16686/jaeger/ui |
| OpenSearch | http://127.0.0.1:19200 |
| flagd evaluation / health | 18013 / 18014 |
| flagd UI（仅供 acceptance harness 控制） | http://127.0.0.1:14000 |
| Locust | http://127.0.0.1:18089 |

可通过 override 中 `OTEL_DEMO_*_PORT` 修改。Kafka、PostgreSQL、Valkey 不暴露宿主端口，通过 `compose exec` 健康检查。healthcheck 同时检查 frontend、Locust running/spawning、Prometheus API、Jaeger API、OpenSearch、flagd readiness，写入 `artifacts/otel_demo/healthcheck.json`。

## OpsPilot 配置

在运行 worker 的 shell/.env 中设置（不止是 API 进程）：

```bash
export OPSPILOT_OBSERVATION_BACKEND=otel_demo
export OPSPILOT_PROMETHEUS_URL=http://127.0.0.1:19090
export OPSPILOT_JAEGER_URL=http://127.0.0.1:16686/jaeger/ui
export OPSPILOT_OPENSEARCH_URL=http://127.0.0.1:19200
export OPSPILOT_TOOL_TIMEOUT_SECONDS=30
export OPSPILOT_TELEMETRY_TIMEOUT_SECONDS=5
```

URL 无代码默认值，选择 live 时必须显式提供三者。未显式设置工具预算时，live 模式默认 30 秒；Mock 默认不变，显式配置始终优先（复制 `.env.example` 后需将其中 0.2 秒改成 30 秒）。容器内的 `127.0.0.1` 指容器自身：若 worker 在 Docker 内，请通过自有网络/代理提供可达 backend 地址，并把这些环境变量传入 worker（主 Compose 不自动继承 shell/.env 的所有变量）。本集成默认端口只面向宿主运行的 worker。

默认查询告警前 900 秒、后 60 秒；`OPSPILOT_TELEMETRY_WINDOW_BEFORE_SECONDS` / `...AFTER_SECONDS` / `...STEP_SECONDS` 控制区间及步长。实时告警之后尚未产生的观测不会被填充。历史样本严格取 alert timestamp 之前，当前值使用窗口最后一个真实有限样本。历史保留取决于后端；Jaeger 为有界内存存储。

## Metrics 发现与适配

```bash
python scripts/otel_demo/discover_metrics.py --service frontend
# 或指定历史窗口（带时区）
python scripts/otel_demo/discover_metrics.py --service checkout --timestamp 2026-10-03T12:00:00+00:00
```

输出 `artifacts/otel_demo/discovery/metrics.json`：真实 Prometheus names、labels、series、时间窗口。Provider 每次读取先 discovery，再只查询实际存在且有对应服务标签的 known alias；绝不对缺失名称返回假数据，也不把主机指标归属到任意服务。指标缺失意味着 telemetry gap，不代表正常。

本环境没有 Docker，**尚未实际发现任何 Prometheus metrics**。下表名称是源码/receiver metadata 核实后的候选映射，运行时必须通过 discovery 验证，不是已采集结果。

| OpsPilot Tool | 真实数据源与转换 |
| --- | --- |
| metrics.query | Prometheus range API；`traces_span_metrics_calls_total` 的 server span rate → qps；ERROR/server rate 比值 ×100 → error_rate；duration histogram quantile → tp95/tp99（ms）；container CPU utilization、memory percent ÷100 →比例；CPU 在多核超过100%时上限截为1，避免现有 Evidence 的百分比兼容规则误读 |
| traces.query | Jaeger `/api/traces`，按 service/start/end/limit 查询；client 另支持 error/minDuration 筛选 |
| logs.query | OpenSearch `otel-logs-*`，service keyword + observedTimestamp 窗口，优先 ERROR/WARN/FATAL/exception/timeout；最多50条，每条 message 最多2000字符 |
| kafka.lag | `kafka_consumer_group_lag_sum`，否则 partition-level `kafka_consumer_group_lag` 求和；不重复累加两者 |
| db.connections | `postgresql_connection_max_connections` / `postgresql_connection_max` → max_connections；不把包含空闲/后台进程的 `postgresql_backends` 冒充 active_connections |
| db.slowlog | Jaeger 中 `db.system[.name]=postgresql` 且 duration >1000ms 的真实 span；返回有限采样集中的 slow_query_count/slow_queries，不是全库慢日志总量 |
| db.replication | 单实例无 replica，空 observations，不查询或制造 lag |
| redis.memory | Redis receiver used-memory bytes；仅实际存在 maxmemory 且大于0时计算比例。上游默认未启用 maxmemory，通常仅有 used_memory |
| redis.hotkeys | keyspace hits/misses counter rate → hit_rate_percent；没有真实热点键列表则不输出 hotkeys |
| rpc.metrics | Jaeger 中目标服务的 client spans，按 span.start_time 分离前后窗口并去重；返回 error_rate、call_volume，前后均有样本时返回 latency_ms/baseline_latency_ms；所有当前样本都有协议状态时才计算 timeout_rate（gRPC deadline exceeded / HTTP 504） |
| changes.query | 无部署变更数据源，空 observations，绝不读取 fault flags |
| alerts.query / topology.query | 尚无独立数据源，空 observations；依赖链保留在 traces |

Known latency aliases 包括 `traces_span_metrics_duration_milliseconds_bucket` 和 `http_server_request_duration_seconds_bucket`。Kafka/Valkey receiver 指标代表本 demo 唯一共享实例；没有将服务名强行用于基础设施指标筛选。Prometheus alias 依据固定上游 dashboards 和 [Collector v0.160.0 receiver metadata](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.160.0/receiver)。未来新增指标/标签约定需要先查看 discovery，再增加明确语义映射。

Jaeger adapter 解析 processes、CHILD_OF references 和 status tags，微秒转毫秒，输出已有 native traces/spans schema，保留 trace_id/span_id/service/operation/parent_span_id/duration_ms/status。已有 detect_span_anomalies 重建 frontend→checkout→payment 路径。PostgreSQL 标签只用于适配层识别 DB spans。

OpenSearch adapter 使用 upstream Grafana datasource 的 `observedTimestamp` 字段，输出 timestamp/service/severity/message，过滤与排序在后端完成。业务日志本身可能包含敏感数据，本适配器只保留必要字段和有限文本。

## 失败语义与数据隔离

连接/HTTP/响应结构错误分别归一化为 `prometheus_unavailable`、`jaeger_unavailable`、`opensearch_unavailable`；ToolExecutor 产生 ERROR，现有 report builder 根据失败调用产生 degraded。成功查询无记录才返回空。整体工具预算超时仍使用既有 `tool_timeout`。

Harness 后续提交的 Alert 必须只描述 symptom（例如 `checkout error rate elevated`），ground truth 留在独立 benchmark metadata；不得把 fault flag、真实根因放入 description/labels/signals。本阶段未实现 fault harness，也未跑 fault/LLM 实验。

## 验证

```bash
pytest tests/contract/test_otel_demo_provider.py -q
# 固定版本 demo 启动并积累遥测后，使用以上 live 环境变量：
OPSPILOT_OTEL_LIVE_TEST=1 pytest tests/integration/test_otel_demo_live.py -q
```

单测 fixtures 是人工构造的真实 API **格式**，并非 live 捕获数据；验证 adapter→ObservationOutput→Evidence 和异常、隔离、单位、时间窗口。Live smoke 执行全部现有工具，失败即失败，L1 metrics/logs/traces 要求非空；领域能力允许有已说明的空值，Kafka lag、DB connection max、Valkey used memory / hit rate 也要求真实字段；同时保存实际 discovery 和逐字段 `telemetry_gaps.json`，成功查询不等同于字段齐全。实际 payload 写入 artifact 供审查。

本次 Docker 集成 **SKIPPED：执行环境无 Docker CLI/daemon**，未运行真实 discovery、部署 healthcheck 或 live smoke。详见 `artifacts/otel_demo/validation.json`。主要缺口：活跃 DB 连接计数、replication、全量慢查询、Valkey maxmemory/热点键、缺少协议状态的 RPC 样本、部署变更源、独立拓扑/告警源。日志和 trace 采样/内存保留、metric label/version 差异会影响覆盖率；没有调 RCA 阈值来掩盖这些缺口。


## 2026-10-04 复查补齐

- RPC 只统计目标服务、窗口内的 client span，去除 internal span 和重复返回的 trace/span；历史基线严格早于告警。
- 明确协议状态支持超时识别；缺少状态或历史数据时不伪造 timeout_rate / baseline。
- Prometheus 带点标签使用 UTF-8 引号语法，选择器的全部条件必须在同一条 discovery series 上成立。
- 后端畸形记录归一化为对应 unavailable 错误；live 工具预算提供可用默认值。
- 现有 clone 的相对路径转绝对路径；启动仍要求固定版本且干净，停止/检查不因 flagd UI 修改而被阻断，也不会 clone。
- 新增脚本测试使用临时本地 Git 仓库和假的 Docker CLI，仅验证脚本行为，绝不算真实 Compose/Docker 验证。

实现依据：[OpenTelemetry gRPC 语义约定](https://opentelemetry.io/docs/specs/semconv/rpc/grpc/)、[Prometheus 标签语法](https://prometheus.io/docs/concepts/data_model/)。
