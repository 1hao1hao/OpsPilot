# OpenTelemetry Astronomy Shop 本地真实环境验收

最新验收：2026-10-04T21:51:58.955770+08:00。**LIVE_INTEGRATION = PASS**。
后附服务器/迁移/重启前的历史记录；其中 FAIL 是当时的状态，当前结论以本节及 `acceptance_summary.json` 为准。

## 环境与部署

Windows 11 Pro build 26200，i7-14700KF（20 核/28 线程）、约 31.8 GiB RAM；工作目录 `D:/deeprca/DeepRCA-Agent-master`。分支 `agent/github-actions-validation`、HEAD `fcc74f5`，`git fsck --full` 退出码 0，仅 dangling tree。原有未提交、删除和未跟踪文件均保留，没有 reset/clean。
六个要求文件存在；原 Linux `.venv` 已备份为 `.venv.linux-backup-20261004175549.bak`，新建本机 Python 3.12.10 环境并安装 `.[dev]`，pip check 通过。迁移 audit 检查 LF、权限、符号链接和 `.env` 服务器路径，没有打印密钥。

Docker Desktop 4.93.0，Docker client/server 29.8.1，Compose v5.5.1，Linux daemon/WSL2 正常，`docker info` 和实际 `docker run --rm hello-world` 均通过。此次 Windows 功能启用后的一次重启使 hypervisor 加载，不需要再次重启。
Demo 固定 commit `7ea09b865a9b9d411b59459b15133d84f8c2784a`，Demo 镜像 3.1.0，Collector 0.160.0；实际 Jaeger 2.21.0。使用已有 bootstrap/start/compose 脚本和 core + full + observability 四层 Compose，28 个服务运行，全部定义的健康检查正常：

accounting, ad, astronomy-db, cart, checkout, currency, email, flagd, flagd-ui, fraud-detection, frontend, frontend-proxy, grafana, image-provider, jaeger, kafka, load-generator, opamp-server, opensearch, otel-collector, payment, product-catalog, prometheus, quote, recommendation, shipping, telemetry-docs, valkey-cart

Git 访问使用本机现有代理；GHCR 的原生 pull 多次 header timeout，改为从官方 GHCR 下载原始 OCI 镜像、校验 manifest/config/layer SHA-256 后 `docker load`，24 个 GHCR 镜像的校验证据在 `images/`，全部 28 个镜像最终身份在 `images_final.json`。没有使用替代镜像或重建 Demo。
checkout 在 28 线程机器上、上游 20M/16MiB 配置下出现约 400% CPU、无法监听及健康检查超时；通过项目 override 设置本机 128M/96MiB、GOMAXPROCS=2 后稳定，Grafana 同样限制 Go 并发以消除高 CPU。没有修改 upstream tracked source；最终 git status 为空。
flagd 使用 upstream flags 的独立运行副本 `.external/opspilot-flags`；控制面读回确认开关生效，最终所有故障 off。flagd UI 的运行副本不写回 upstream。

## 连续正常流量与真实数据

frontend 和 load-generator 正常。两个真实采样之间等待 45 秒，采样查询本身也消耗时间；指标/trace/log 均继续产生：

| 后端 | 第一次 | 第二次 |
| --- | --- | --- |
| Prometheus span 请求计数 | 58696 | 62310 |
| OpenSearch 实际日志数 | 12370 | 14106 |
| 最新日志 observedTimestamp | 2026-10-04T13:37:40.882024323Z | 2026-10-04T13:38:50.364475961Z |
| 最新 Jaeger span start（epoch 秒） | 1791121038.198 | 1791121107.929443 |

两次 Jaeger 查询各返回 20 条真实 trace，OpenSearch 样本包含 frontend-proxy/load-generator 的 service.name，Locust stats 和 HTTP frontend 结果均已保存。验收没有仅凭 HTTP 200 判定后端有数据。

## Metric discovery 与最小适配修复

正式 discovery 发现 483 个名称；真实标签包括 `service_name`, `container_name`, `span_kind`, `status_code`, `group`, `topic`, `postgresql_database_name`。原始完整 names/labels/series 在 `normal_verified/discovery.json`，每个实际 PromQL、matrix 和 normalized observations 在对应 ToolResult/raw。

请求/错误：`traces_span_metrics_calls_total`；延迟：`traces_span_metrics_duration_milliseconds_bucket`；CPU：`container_cpu_utilization_ratio` / `jvm_cpu_recent_utilization_ratio`；内存：`container_memory_percent_ratio`；PostgreSQL：`postgresql_connection_max`；Kafka：`kafka_consumer_group_lag_sum_ratio`（优先合计，避免重复计数）；Valkey：`redis_memory_used_bytes`, `redis_keyspace_hits_total`, `redis_keyspace_misses_total`。
根据实测修复 CPU/memory/Kafka 的 `_ratio` 别名。容器 CPU/memory 实际样本仍为百分数，需除 100；JVM/process CPU 是真实比例，除 1。ad 的容器资源 series 缺失，使用已发现的 service-scoped JVM CPU，不伪造 container 指标。
Jaeger 2.21.0 旧 `/api/traces` 搜索返回 404，改为仅在此 404 时使用 `/api/v3/traces`，并将 OTLP 转入现有 normalized trace 契约；其他错误仍失败。只有明确的 `No traces found` 响应才归为空，未知 404、500、格式错误均失败。新增五项兼容性测试，保留旧接口路径。

正常 frontend 的实际 L1 PromQL：

- `qps`: `sum(rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER",service_name="frontend"}[2m]))`
- `error_rate`: `100 * sum(rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR",service_name="frontend"}[2m])) / sum(rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER",service_name="frontend"}[2m]))`
- `tp95`: `1 * histogram_quantile(0.95, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket{span_kind="SPAN_KIND_SERVER",service_name="frontend"}[2m])))`
- `tp99`: `1 * histogram_quantile(0.99, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket{span_kind="SPAN_KIND_SERVER",service_name="frontend"}[2m])))`
- `cpu_usage`: `clamp_max(max(container_cpu_utilization_ratio{container_name="frontend"}) / 100, 1)`
- `memory_usage`: `max(container_memory_percent_ratio{container_name="frontend"}) / 100`

## 每个工具的 live smoke

| Tool | 正常状态 | 分类 |
| --- | --- | --- |
| `alerts.query` | EMPTY_BY_DESIGN | B |
| `changes.query` | EMPTY_BY_DESIGN | B |
| `db.connections` | PASS | A |
| `db.replication` | EMPTY_BY_DESIGN | B |
| `db.slowlog` | EMPTY_BY_DESIGN | B |
| `kafka.lag` | PASS | A |
| `logs.query` | PASS | A |
| `metrics.query` | PASS | A |
| `redis.hotkeys` | PASS | A |
| `redis.memory` | PASS | A |
| `rpc.metrics` | PASS | A |
| `topology.query` | EMPTY_BY_DESIGN | B |
| `traces.query` | PASS | A |

A = 查询成功且有观测；B = 成功但此部署/时间窗没有对应观测；C = 后端/查询错误，记 FAIL。正常最终验收无 C。alerts/changes/topology 无事件或库存事实源，db.replication 无 replica，db.slowlog 未发现采样 trace 中大于 1 秒的 PostgreSQL span。EMPTY_BY_DESIGN 不代表完整历史中绝无慢查询。Kafka lag=0 是有效数据，不当成空。
`normal_verified/ToolResults.json` 对每个工具保存完整 ToolResult、normalized observations 和 raw_backend_refs；`raw_manifest.json` 映射至 raw 后端响应。最初 native pull、启动及 `normal/` 的失败记录保留；最终以 `normal_verified/` 为准。最终适配器的旧 Jaeger 404 探测是已识别的协议差异，若新接口也失败则仍是 C。

## 一个 Fault smoke 与一次真实 RCA

全部故障 off → 真实流量基线 → 开启 adHighCpu → 等待 120 秒 → metrics/traces 工具 → 当前 RCA → finally 关闭故障 → 等待并采集恢复。
相同实际 PromQL：`clamp_max(max(jvm_cpu_recent_utilization_ratio{service_name="ad"}) / 1, 1)`。

| 阶段 | JVM CPU 比例 |
| --- | --- |
| baseline | 0.000304528 |
| fault | 0.139741598 |
| settled recovery | 0.000106227 |

CPU 增加约 458.9 倍，关闭后恢复低值；初次恢复采样仍有导出/采样滞后，保留 `recovery_ad/`，稳定恢复记录在 `recovery_settled/`。CPU 的 JVM 比例以整机 CPU 为分母，不能与 Docker 多核百分比混用。trace 查询成功，但不宣称延迟必须上升。没有尝试第二个 fault 或构建 benchmark。

symptom-only Alert 描述 CPU 上升且持续请求，没有 flag 名、ground truth 或预置 signals。沿用当前 OpsPilotWorkflow/AdaptiveInvestigator、ToolExecutor、Evidence 和报告构建链。真实 RCA completed，7 条 Evidence，5 个 ToolResult，6 个动作，LLM 禁用、planner 使用现有 fallback。
如实保存结果：Top-1 `rpc_timeout`，Top-2 `resource_saturation`，均置信度 0.98；最终 Gate insufficient（独立来源 1、margin 0），预算耗尽；报告字段 degraded=false。这证明执行链贯通，不证明首位判断正确，也不是 Adaptive Accuracy。没有修改 Planner/Fallback、Evidence Gate、Ranker、Runtime 或旧 Benchmark 6/21。

## 验证、工件与限制

全套本地 pytest：**221 passed, 4 skipped**（216 原有通过 + 5 项新增 Jaeger 兼容测试）；真实 opt-in live 集成测试单独执行 **1 passed**。pip check 和本次涉及文件 ruff 通过。离线测试四个 skip 不冒充真实验收；仍有一条既有 Starlette/httpx 弃用警告。
`git diff --check` 发现原有 `docs/plan.md:69` 的尾部空格；该文件的用户修改保持原样。

主工件目录：`artifacts/otel_demo/live_acceptance/local_windows/`。

- `acceptance_summary.json`：带断言的最终结果；`docker_version.txt`, `compose_version.txt`, `docker_info.txt`, `hello_world.txt`。
- `start_checkout_resources.txt`, `compose_ps_final.jsonl`, `healthcheck_final.json`, `docker_stats_final.txt`, `upstream_head.txt`, `upstream_status_final.txt`。
- `traffic/sample_1.json`, `sample_2.json` 及 raw；`normal_verified/`, `baseline_ad/`, `fault_ad/`, `recovery_ad/`, `recovery_settled/` 的 discovery、ToolResults、raw_manifest、raw。
- `control/` 的开启、关闭和最终真实读回；`images/`、`images_final.json`；下载和失败历史 txt。
- `rca/Alert.json`, `ToolResults.json`, `Evidence.json`, `TopK.json`, `Gate.json`, `ActionHistory.json`, `DiagnosisReport.json`, `execution.json`, `raw_manifest.json` 及 raw。
- `pytest_final.txt`, `pytest_live.txt`, `pip_check_final.txt`, `ruff_final.txt`；`artifacts/otel_demo/live_smoke.json`, `telemetry_gaps.json`, `discovery/metrics.json`；迁移原始检查在 `artifacts/otel_demo/local_migration/`。

telemetry gaps：ad 缺容器 CPU/memory（本次 CPU 使用 JVM 观测）；PostgreSQL active_connections 不存在，只有 max_connections；Valkey 未导出 maxmemory/百分比和 hotkeys；frontend RPC 样本无完整协议状态，未伪造 timeout_rate；ad 无出站 RPC，rpc.metrics 为空。无部署/事件/真实 topology inventory，无 PostgreSQL replica。warmup 时 OpenSearch 尚未就绪，Collector 丢失过早期日志；正式验收仅使用后端就绪后的新日志，不声称补回。日志/trace 均有采样和限额，scope/window 之外的数据不代表被完整扫描。

未验证生产部署、长期稳定性、正式 Accuracy/旧 Benchmark、真实 LLM、6 fault 数据集和 Runtime 持久化服务；这些不在本任务范围。

本机启动（PowerShell，仅当前进程允许脚本执行）：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
. ./scripts/otel_demo/local_live_env.ps1
bash scripts/otel_demo/start_demo.sh
```

frontend：<http://127.0.0.1:18080>；Jaeger：<http://127.0.0.1:16686/jaeger/ui/>；Prometheus：<http://127.0.0.1:19090>。停止使用 `bash scripts/otel_demo/stop_demo.sh`（保留 volume）；当前服务保持运行，故障全部关闭。

---

## 以下为保留的历史检查记录

# OpenTelemetry Astronomy Shop 真实环境验收

检查时间：2026-10-04T12:47:38.891219+08:00（Asia/Shanghai）
任务：`plan/task1.5.md`

**LIVE_INTEGRATION = FAIL**

失败层级：环境前置条件。三个必需命令均返回退出码 127、`docker: command not found`。无法获取 Docker/Compose 版本，也无法通过 `docker info` 检查 daemon 及权限。按计划第三节，停止真实实验。此结果不是遥测后端查询失败，也不是适配器测试失败；真实数据链尚未验收。

## 固定版本与实现核查

- Repository：https://github.com/open-telemetry/opentelemetry-demo.git
- 固定 commit：`7ea09b865a9b9d411b59459b15133d84f8c2784a`；Demo image：`3.1.0`；Collector：`0.160.0`；integration schema：`1.0`。
- 已检查现有 bootstrap/start/stop/compose、healthcheck/discovery 脚本：`scripts/otel_demo/`。
- 已检查 PrometheusClient / JaegerClient / OpenSearchClient：`src/opspilot/observations/clients.py`；live provider：`src/opspilot/observations/provider.py`。
- 已检查 settings/backend 选择与 `tests/integration/test_otel_demo_live.py`。沿用现有实现，没有新增第二套 integration。
- 本次没有启动 bootstrap 或改动 upstream tracked source，没有修改 Planner、Fallback、Evidence Gate、Ranker、Runtime。

## Docker 与服务

| 命令 | 退出码 | 实际输出 |
| --- | --- | --- |
| `docker --version` | 127 | docker: command not found |
| `docker compose version` | 127 | docker: command not found |
| `docker info` | 127 | docker: command not found |

本次启动服务：无。core + full + observability 启动、health 稳定性、`docker compose ps` 均未执行；没有生成虚假的 ps 工件。
目标 frontend、load-generator、flagd、otel-collector、Prometheus、Jaeger、OpenSearch、PostgreSQL、Kafka、Valkey 均未进行运行态验收。已有部署状态未知，不能把“本次未启动”解释为“当前主机一定没有服务”。

## 正常流量、后端与指标发现

Prometheus / Jaeger / OpenSearch：全部 **NOT_RUN**。没有执行 HTTP health/live query，也没有证明正常流量、连续变化的指标、新 trace 或新 log。

实际发现 metrics / labels / service.name：**未查询，未知**；不是查询成功的空结果。request rate、error rate、latency、CPU、memory、PostgreSQL、Kafka、Valkey 指标均未确认。
实际执行的 PromQL：无。没有根据猜测修改 metric alias，也没有用 fixture 或历史离线测试生成本次 discovery。

## OpsPilot Tool live smoke

以下 FAIL 表示本次验收未通过，执行状态均为 NOT_RUN；**不表示已发出查询后返回 ERROR，也不能归为 EMPTY_BY_DESIGN**。原始后端响应、normalized observations、ToolResult 均未产生；结构化记录以 null 表示未执行，不伪造 ToolResult。

| Tool | 验收状态 | 执行状态/原因 |
| --- | --- | --- |
| `metrics.query` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `logs.query` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `traces.query` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `topology.query` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `changes.query` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `alerts.query` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `db.replication` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `db.slowlog` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `db.connections` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `redis.memory` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `redis.hotkeys` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `kafka.lag` | FAIL | NOT_RUN：Docker 前置检查失败 |
| `rpc.metrics` | FAIL | NOT_RUN：Docker 前置检查失败 |

代码中 topology/changes/alerts 没有独立真实数据源、db.replication 为单 PostgreSQL 实例无副本；这些是静态能力说明，不是本次 live 观测结果。

## Fault smoke 与完整 RCA

- 实际使用 flag：无；没有开启或关闭任何故障，也未确认所有 fault off。
- Normal baseline、fault 后采样及遥测差异：未采集，不能声称有差异。
- symptom-only Alert、ToolResults、Evidence、Top-K、Gate、ActionHistory、DiagnosisReport：全部未生成。
- Planner 模式：没有运行；不能声称本次使用了真实 LLM 或 fallback。
- 未运行正式 Accuracy、6 Fault Benchmark、付费 LLM 调用或旧 Benchmark 6/21 问题的修复。

## 工件

本次工件仅包含真实执行过的环境检查、固定版本快照、未执行状态：

- [environment.json](../artifacts/otel_demo/live_acceptance/20261004T124738+0800/environment.json)：每个命令的退出码、stdout、stderr、检查时间。
- [environment.txt](../artifacts/otel_demo/live_acceptance/20261004T124738+0800/environment.txt)：原始命令输出。
- [version.json](../artifacts/otel_demo/live_acceptance/20261004T124738+0800/version.json)：仓库已有固定版本快照。
- [acceptance.json](../artifacts/otel_demo/live_acceptance/20261004T124738+0800/acceptance.json)：验收标准与逐 Tool 未执行状态。

前阶段离线 fixtures、unit tests 和评测结果未作为本次 live 证据。

## Telemetry gap 与未验证项

运行态 gap：尚未测量。已有实现说明的 active DB connections、replication、全量慢查询、Valkey maxmemory/hotkeys、缺失协议状态的 RPC timeout、独立 topology/change/alert 数据源限制，仍需真实环境确认；见 [集成指南](../integrations/opentelemetry_demo/README.md)。

以下五个 PASS 必要条件全部未执行：Prometheus live query、Jaeger live query、OpenSearch live query、一个故障产生真实观测差异、OpsPilot 完整 RCA 成功运行。因此 **LIVE_INTEGRATION = FAIL**，不能标记任务真实验收完成。

继续条件：当前执行环境提供可运行的 Docker CLI、Compose 与有权限访问的 daemon；然后从环境检查开始，沿用 `scripts/otel_demo/start_demo.sh` 启动固定版本，再执行正常流量、discovery、逐 Tool、单 Fault smoke 和一次完整 RCA。仅安装 CLI 而没有可用 daemon 仍不满足条件。

## 后续 Docker 安装进展

用户随后授权部署 Docker。已在当前账号 `~/.local/bin` 安装 Docker 29.8.2 与 Rootless 组件，Compose 5.6.0 安装于 `~/.docker/cli-plugins`；未修改系统文件或已有 shell 配置。
`docker --version`、`docker compose version`、`dockerd --version` 均通过，现有固定版本 Demo 的 `compose config --quiet` 也通过。

**daemon 仍不可用**：官方 `dockerd-rootless-setuptool.sh check` 确认 `/etc/subuid`、`/etc/subgid` 缺少当前账号的65536个映射；`newuidmap/newgidmap` 已有正确 capabilities。当前账号 sudo 需要密码，用户说明无法联系管理员修改配置。因此没有安装/启动 daemon 服务，`docker info` 返回无法连接 socket。安装客户端和配置解析通过不等于真实集成通过，本文 `LIVE_INTEGRATION = FAIL` 仍成立。

新工件：[安装版本与下载校验](../artifacts/otel_demo/docker_setup/installation.json)、[逐命令验证与阻塞原因](../artifacts/otel_demo/docker_setup/verification.json)。用户拟改在本地电脑部署，等待其系统、CPU、内存信息。

## 本地 Windows 迁移验收（2026-10-04，Asia/Shanghai）

项目目录已核实为 `D:\deeprca\DeepRCA-Agent-master`。本机为 Windows 11 Pro 64 位（build 26200），Intel i7-14700KF，20 核 / 28 线程，31.8 GiB 内存；固件虚拟化已开启。

Git 分支为 `agent/github-actions-validation`，HEAD 为 `fcc74f5`。`git status --short` 已记录全部修改、删除和未跟踪文件；`git fsck --full` 退出码为 0，仅报告 dangling tree，未报告对象损坏。本次未 reset、clean、checkout 原项目或清理原有文件。Git 本仓库配置为 `core.autocrlf=input`、`core.eol=lf`，避免后续操作改写 Linux 脚本换行。

六个指定文件均存在。原服务器 Python 3.11.7 环境已原样改名为 `.venv.linux-backup-20261004175549.bak`；新 `.venv` 使用本机 Python 3.12.10，已安装 `.[dev]`，`pip check` 通过。

脚本检查：`scripts/` 下所有 shell 脚本均为 LF，无 CRLF；项目审计范围内没有符号链接，Git 索引也没有 120000 符号链接条目。Windows 的 `os.access(..., X_OK)` 不代表 Linux POSIX 执行位；Git 中已有两个 tracked shell 脚本均为 100755，实际 Git Bash 执行也已通过部署安全测试。`.env` 存在，审计未发现服务器绝对路径，未输出任何变量值或密钥。迁移审计排除了备份虚拟环境、`.git` 和外部依赖目录。

首次本地测试为 212 passed / 4 failed / 4 skipped；失败为部署脚本测试对 POSIX PATH、`python3` 和路径格式的假定。已仅修复部署脚本及其测试的 Windows 兼容性：使用实际 Python 解释器、平台 PATH 分隔符、LF mock 脚本和 Git Bash 路径转换；bootstrap 读取版本时移除 Windows Python 的 CR。最终全套测试 **216 passed / 4 skipped**，退出码 0；另有一条依赖库弃用警告。四个 skip 为 opt-in live / PostgreSQL / Redis 测试，不作为真实部署通过证据。未修改 Planner/Fallback、Evidence Gate、Ranker 或 Runtime，未处理旧 Benchmark 覆盖率。

本机安装了 Git for Windows 2.55.0、Docker Desktop 4.93.0；`docker --version` 为 29.8.1，`docker compose version` 为 v5.5.1。Docker Desktop 已启动，但 `docker info` 和实际 `docker run --rm hello-world` 均退出码 1，Linux engine 返回 HTTP 500，测试容器未成功运行。

已通过 UAC 管理员进程启用 `VirtualMachinePlatform` 和 `Microsoft-Windows-Subsystem-Linux`，两项均明确返回 **RestartNeeded=True**。没有自动重启用户电脑。WSL 2.7.13 软件包首次以普通令牌安装因管理员权限失败；改用管理员进程后安装成功，退出码 0，`Get-AppxPackage` 确认版本 2.7.13.0、状态正常。安装程序也明确提示虚拟机平台需要重启才能运行 WSL。本机尚未满足 daemon 前置条件，按计划第三节停止真实实验。

本地状态仍为 **LIVE_INTEGRATION = FAIL**：没有启动 Astronomy Shop、运行 discovery、采正常遥测、开启 fault 或运行真实 RCA。前面的服务器历史结果保留；本节记录本机的新状态。

本地工件目录：`artifacts/otel_demo/local_migration/`，包括 `migration_audit.json`、`git_status.txt`、`git_fsck.txt`、`python_dependencies.txt`、`pytest_before_fix.txt`、最终 `pytest.txt` / `pytest.xml`、`docker_version.txt`、`compose_version.txt`、`docker_info.txt`、`hello_world.txt` 和管理员 `wsl_features.txt`。

继续条件：保存其他工作并重启 Windows，再启动 Docker Desktop，复核 `wsl --version`、`docker info` 及 `docker run --rm hello-world`。成功后在 Git Bash 中设置 `PYTHON="$PWD/.venv/Scripts/python.exe"`，沿用 `scripts/otel_demo/start_demo.sh`，再执行本计划的正常流量、指标发现、逐 Tool、单 fault smoke 和完整 RCA 验收。重启后仍须重新核验 WSL 包和 daemon，不能仅凭安装记录判断可用。

### 对“无需重启”的专项核验

用户提出避免反复重启后，已再次实测并用 UAC 管理员进程检查系统功能与启动配置：

- 本机最后启动时间为 2026-10-04 17:24:59（Asia/Shanghai），早于本次安装/启用虚拟机功能；尚未经历功能启用后的重启。
- `VirtualMachinePlatform` 和 `Microsoft-Windows-Subsystem-Linux` 当前配置均为 Enabled，但 `HypervisorPresent=False`，组件服务 `RebootPending=True`，固件虚拟化为 True。
- BCD 查询成功，未发现 `hypervisorlaunchtype Off`；本次没有修改启动配置。
- `wsl --version` 输出软件包与附带内核版本；这不是实际成功执行 Linux `uname -a` 的证据。`wsl --list --verbose` 当前没有已注册发行版。Docker Desktop 无需另装 Ubuntu，但仍依赖已加载的 WSL2 虚拟化底座。
- Docker backend 日志明确报 `Virtual Machine Platform not enabled` / `No virtualization available`。

结论：本机尚不满足“WSL2 已经运行”的前提；Docker 已安装，继续重复安装不能替代加载虚拟机功能所需的系统重启。下一步为 Windows“重启”，之后以 hypervisor 状态、`docker info` 和实际容器结果验收；若仍失败，应基于新的具体错误继续定位，不以反复重启作为排障方法。未自动重启电脑。

新增工件：`virtualization_diagnosis.json`、`boot_configuration.txt`，位于上述本地工件目录。
