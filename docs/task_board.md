# OpsPilot Three-Stage Task Board

## 1. 使用方式

本任务板把 DeepRCA -> OpsPilot 重构压缩为三个阶段、六个完整纵向任务。每次 Codex 执行只领取一个任务；任务必须一次完成代码、接入、测试、验证和工件，不再把 schema、adapter、测试或报告拆成独立等待项。

状态：`TODO`、`IN PROGRESS`、`DONE`。只有验收命令真实通过并保存工件后才能改为 `DONE`；部分完成保留 `IN PROGRESS` 并列出未通过项。

规划基线（2026-08-11）：

- `plan_new.md` 为范围主依据，`plan.md` 只补充细节；
- 当前目录不是 Git 仓库，实施前后仍需用文件 diff 工具谨慎核对修改；
- 现有 `pytest -q tests/unit` 在收集阶段因项目未安装且缺少依赖失败，尚无可信绿色基线；
- Stage 1、Stage 2 以及 Stage 3 的实现/实验已于 2026-08-12 完成；Stage 3 只剩当前主机缺少 Docker daemon 导致的全新 Compose 容器复现验证，测试、逐 case prediction、恢复时间线和最终证据索引均已保存。

## 2. 三阶段总览

| 阶段 | 目标 | 完整任务 | 阶段出口 |
|---|---|---|---|
| Stage 1：场景与核心契约 | 先让现有 RCA 有稳定接口和可重复 baseline | S1-T1、S1-T2 | 同一套场景可稳定运行，输出逐 case baseline 与正式指标 |
| Stage 2：可恢复 Runtime | 把 Agent 从 API 进程移到持久化异步 Worker | S2-T1、S2-T2 | API 快速返回、Worker 执行、重启后从 Checkpoint 恢复，重复请求安全 |
| Stage 3：故障与证据闭环 | 用真实故障注入、Regression 和 Compose 证明最终形态 | S3-T1、S3-T2 | 四个 Demo 和所有简历数字均有可复查工件 |

依赖顺序：

```text
S1-T1 -> S1-T2 -> S2-T1 -> S2-T2 -> S3-T1 -> S3-T2
```

不并行跨阶段开发。Stage 1 的 schema 和 Benchmark 是后续重构的防退化契约。

---

## Stage 1：场景与核心契约

### S1-T1 `[DONE]` 统一 RCA 契约并完成最小迁移链

项目背景：在现有 DeepRCA 工作流上建立 OpsPilot 的稳定输入输出边界，保留算法行为，把四个领域 Expert 收敛为 Coordinator 可调用的领域 Tool。

开始前阅读：

- `docs/project_brief.md`、`docs/architecture.md`、`docs/coding_rules.md`；
- `src/deeprca/api/routes.py`、`graph/state.py`、`graph/main_graph.py`；
- `agents/coordinator.py`、`agents/root_cause.py`、`graph/subgraphs/*`；
- `models/*`、`tools/*`、`detection/*` 和相关 unit tests；
- 当前文件变更状态。

系统位置：

```text
Alert -> OpsPilot schema/adapter -> Coordinator -> Registered Tools
-> Evidence -> deterministic RCA -> Root Cause Agent -> DiagnosisReport
```

输入：现有 `AlertEvent` 与 8 个 Mock 场景；固定 fake LLM；当前算法测试。

输出：

- `src/opspilot/models` 下稳定 Alert、Plan、Tool、Evidence、RootCause、Report schema；
- `src/opspilot/tools` 下 Registry/Executor 和 DB/Redis/Kafka/RPC/通用领域 Tool；
- 迁移后的 Coordinator + Root Cause 两 Agent 工作流；
- `/api/v1/analyze` 兼容 adapter；
- characterization、unit、contract 测试。

原框架已有：LangGraph 主链、领域采集逻辑、确定性检测、专家规则、报告与 Mock API。

本任务新增：统一 schema、稳定 `root_cause_type`/Evidence/ToolResult、Tool Registry/Executor、两 Agent 边界和兼容适配。

当天一次性完成：

1. 修复可复现安装入口，记录完整 unit test 基线；
2. 冻结 schema 和领域枚举，显式解决 `alert.metrics`、Evidence 维度与 `mafka/kafka` 口径冲突；
3. 为现有核心行为补 characterization test；
4. 将 DB/Redis/Kafka/RPC 分析封装为 Tool，Agent 只通过注册名调用；
5. 统一 Tool 输入/输出校验、timeout、错误分类和 ToolExecution 内存记录接口；
6. 迁移确定性 RCA 与 Root Cause Agent，保留规则优先和 LLM fallback；
7. 让旧 `/analyze` 经 adapter 运行同一条新链，禁止维护双实现；
8. 更新必要 README/架构事实与测试证据。

验收：

- [x] `python -m pip install -e '.[dev]'` 可复现；
- [x] 旧单元测试与新 unit/contract tests 全部通过；
- [x] Registry 拒绝未知 Tool 与不合法输入；
- [x] fake LLM 下至少 1 个 DB 场景和 1 个正常/无明确根因 fixture 输出合法报告；
- [x] 新旧入口对同一 fixture 的 `root_cause_type` 和 Evidence 类型一致；
- [x] Agent 代码中无直接 `httpx`/Redis/PostgreSQL 客户端；
- [x] 工件记录测试命令、case 与真实结果。

本任务不实现：PostgreSQL、Redis Queue、Worker、Checkpoint、正式 30-50 case Benchmark。

建议验证命令：

```bash
ruff check src tests
pytest -q tests/unit tests/contract
```

标准工件：`artifacts/stage1/contracts/summary.md` 和测试日志摘要。

### S1-T2 `[DONE]` 建立版本化 RCA Dataset 与可运行 Baseline Benchmark

项目背景：把 Mock demo 变成场景契约，使 Runtime 重构前后能够同集比较。

系统位置：

```text
Dataset + RunConfig -> Scenario Loader -> System Prediction
-> Metrics -> Failure Analysis -> Regression candidate
```

输入：8 个现有预设场景、S1-T1 统一 schema、固定 seed、现有 RCA 系统入口。

输出：

- `benchmarks/datasets/rca/v1/` 的 manifest、dev 和 frozen-test case；
- `benchmarks/configs/` 的 DeepRCA baseline 与 OpsPilot hybrid 配置；
- dataset loader、runner、metrics、report；
- 标准 evaluation artifacts。

原框架已有：场景注入器、Mock 查询 API、8 个 expected root cause、smoke E2E。

本任务新增：正式 case schema、稳定 label、split、逐 case prediction、指标公式、failure taxonomy 和命令行入口。

当天一次性完成：

1. 把现有 8 个场景转换为可追踪 seed case，不直接复制自由文本标签；
2. 扩展到首版 36 个有效 case，覆盖 DB、Redis、Kafka、RPC、Deploy/Resource、Normal/Noise；
3. 固定 dev 24 / frozen test 12，test 标签冻结；
4. 实现 Hit@1、Hit@3、Evidence Recall、Tool Success、E2E Success、P95、False Positive；
5. runner 真实调用系统并写 predictions，不读取 ground truth 生成答案；
6. 在相同数据和 seed 上运行当前 DeepRCA baseline 与 OpsPilot hybrid；
7. 输出失败分类并选择至少 1 个真实失败转成 regression test；
8. 记录 dataset/version/split/config/case 数/命令/耗时。

验收：

- [x] dataset schema 校验、case ID 唯一、split 无重叠；
- [x] 每类同时有正例、边界/干扰例，Normal/Noise 不少于 6 个；
- [x] 指标单元测试覆盖 0 分母、Top-3 排名、部分 Evidence 命中和失败 Run；
- [x] 两种方案用同一份数据与指标运行；
- [x] `predictions.jsonl` 行数等于实际运行 case 数，失败也保留一行；
- [x] `manifest.json`、`metrics.json`、`failures.jsonl`、`report.md` 齐全；
- [x] 结果只陈述实测值，不承诺虚构提升。

本任务不实现：异步 Worker、Checkpoint、runtime fault、针对 frozen test 调参。

建议验证命令：

```bash
pytest -q tests/evaluation tests/regression
python -m opspilot.evaluation.cli run --config benchmarks/configs/deeprca_baseline.yaml --split dev
python -m opspilot.evaluation.cli run --config benchmarks/configs/opspilot_hybrid.yaml --split dev
```

标准工件：`artifacts/evaluations/<evaluation_id>/`。

Stage 1 出口：S1-T1 与 S1-T2 全部通过；若 baseline 不能稳定复现，不进入 Runtime 重构。

---

## Stage 2：可恢复 Runtime

### S2-T1 `[DONE]` PostgreSQL Run/Step 与异步 Worker 纵向链

项目背景：把完整 RCA 从 FastAPI 进程内后台协程迁移到持久化任务与独立 Worker。

系统位置：

```text
POST /runs -> TaskManager/PostgreSQL -> Redis Queue
-> Worker -> OpsPilot workflow -> PostgreSQL Report -> GET /runs/{id}
```

输入：S1 的统一 Alert/Report schema、同步/进程内 system adapter、PostgreSQL 与 Redis 配置。

输出：Run/Step/Report/RuntimeEvent 表与 migration；Repository/TaskManager；简单 Redis Queue；Worker；新 Run API 与旧 API adapter；Compose 服务。

原框架已有：FastAPI、Redis 配置、LangGraph 执行和 WebSocket。

本任务新增：PostgreSQL 事实源、Run/Step 状态机、API/Worker 解耦、队列传递和持久结果查询。

当天一次性完成：

1. 实现数据库配置、migration 和 Repository transaction；
2. 用数据库唯一约束实现 `request_id` 幂等创建；
3. `POST /runs` 事务创建 `QUEUED` Run 并入队，快速返回 202；
4. Worker 只接收 `run_id`，从 DB 加载输入并推进 `RUNNING`；
5. 把 workflow 稳定节点映射为 Step，保存输出引用；
6. 成功写 Report 和 `SUCCEEDED`，失败写分类和 `FAILED`；
7. 状态、结果、事件与 WebSocket 均读取 PostgreSQL；
8. 将旧 `/analyze` 转发到同一 TaskManager；
9. 加入 API -> Queue -> Worker -> DB integration test 和 Compose smoke。

验收：

- [x] API 不在请求协程内执行完整图；
- [x] 停止 Worker 时 API 仍可提交并查询 `QUEUED` Run；
- [x] 启动 Worker 后 Run 完成且报告可查询；
- [x] 重复 `request_id` 返回同一 `run_id`；并发重复请求也只落一条 Run；
- [x] 非法状态迁移被拒绝且有测试；
- [x] Redis 中只保存队列消息，不作为 Run 结果事实源；
- [x] integration/smoke 测试无真实付费 LLM。

本任务不实现：Checkpoint 恢复、工具重试幂等、Lease、Heartbeat、DLQ。

建议验证命令：

```bash
pytest -q tests/unit tests/contract tests/integration
docker compose up --build api worker postgres redis mock-env
```

标准工件：一次完整 Run 的 DB 状态摘要与事件时间线。

实测（2026-08-12）：Alembic migration 在本地 PostgreSQL 10.19 成功执行；真实 PostgreSQL/Redis + API ASGI + 独立 Worker 子进程链通过。默认分层测试中的 API/Queue/Worker/DB、并发请求幂等和非法迁移用例通过。Compose 已补齐 `api/worker/postgres/redis/mock-env/migrate` 并通过 YAML/服务命令静态检查；当前执行环境有 Compose 客户端但没有 Docker daemon，因此未虚报容器启动命令已运行，本地服务进程覆盖了同一 PostgreSQL、Redis 和进程边界。

### S2-T2 `[DONE]` Step Checkpoint、Retry 与 Tool Idempotency

项目背景：在已解耦 Worker 上实现真正可恢复的 Agent 执行，而不是进程退出后整条重跑。

系统位置：

```text
Worker -> Step -> ToolExecutor -> Step output + Checkpoint
crash -> recovery scan -> requeue -> load checkpoint -> next Step
```

输入：S2-T1 Run/Step、S1 Tool Executor、固定 workflow version、可控故障点。

输出：Checkpoint 表与 Manager、Retry policy、ToolExecution 持久化和 `tool_call_id` 幂等、recovery scan、恢复事件。

当天一次性完成：

1. 为每个稳定 Step 定义最小可序列化 state 和 schema version；
2. 在同一事务写 Step success/output 与 Checkpoint；
3. Worker 启动与周期扫描遗留 `RUNNING` Run，标为 `RETRYING` 后重新入队；
4. 从最新 Checkpoint 继续，跳过已成功 Step；
5. 将 ToolExecution 持久化，以稳定 `tool_call_id` 复用成功结果；
6. 实现 Retryable/Permanent error 和有限 backoff；
7. 工具证据不足时明确 degraded/failed，不吞异常；
8. 覆盖重复投递、工具超时、Checkpoint 版本不兼容和 Worker 中断测试。

验收：

- [x] 在 DB 分析完成、后续 Step 开始时终止 Worker，新 Worker 不重复 DB Tool 成功调用；
- [x] 恢复后同一 Run 最终 `SUCCEEDED`，`recovered_count >= 1`；
- [x] 同一 `tool_call_id` 最多一条成功结果；
- [x] Retryable error 按配置重试，Permanent error 不重试；
- [x] Checkpoint 无法解析时明确失败并保留诊断信息；
- [x] 重复队列消息不会让终态 Run 再执行；
- [x] recovery tests 与 Stage 1 dev Benchmark 通过，无未解释退化。

本任务不实现：跨机器 Lease/Heartbeat、严格优先级、DLQ、自动修复操作。

建议验证命令：

```bash
pytest -q tests/recovery tests/integration
python -m opspilot.evaluation.cli run --config benchmarks/configs/opspilot_hybrid.yaml --split dev
```

标准工件：`artifacts/runs/<run_id>/recovery_timeline.jsonl` 和恢复前后 ToolExecution 摘要。

实测（2026-08-12）：独立 Worker 在 `db.inspect` 成功并保存 Checkpoint、`redis.inspect` Step 已开始后，以受控退出码 97 真正终止；recovery scan 将同一 Run 推进 `RUNNING -> RETRYING -> QUEUED -> RUNNING -> SUCCEEDED`，`recovered_count=1`、Run attempt=2，而 DB `tool_call_id=tool-1d091026332e0171d16c` 的 ToolExecution attempt 始终为 1。恢复工件位于 `artifacts/runs/run-64eae6517a544f95b1f3632addc26324/`。Stage 1 dev Benchmark 新工件 `20260812T045241Z-opspilot_hybrid-dev`：24/24 E2E 成功，Hit@1=1.0、Evidence Recall=1.0、FPR=0.0，与 Stage 1 已记录结果一致。

Stage 2 出口：普通 Run、重复请求和 Worker 中断三条链均通过；若只能从头重跑，不得宣称 Checkpoint Recovery 完成。

---

## Stage 3：故障与证据闭环

### S3-T1 `[DONE]` 可重复 Runtime Fault Injection 与恢复评测

项目背景：把单次恢复演示升级为可重复可靠性实验，证明恢复不是偶然成功。

系统位置：

```text
FaultCase -> Compose/Worker/Tool fault -> Runtime events
-> final Run state -> Reliability metrics -> regression
```

输入：S2 可恢复 Runtime、固定业务场景、可控 Worker/Tool 故障点。

输出：runtime fault case schema、注入器、恢复 runner、Reliability metrics 和失败回归。

当天一次性完成：

1. 定义最小故障矩阵：WorkerCrash、ToolTimeout、ToolHTTP500、DuplicateRequest、DuplicateDelivery；
2. 每个故障固定注入位置、次数、预期终态和是否允许 degraded；
3. 运行多轮而非单次 demo，记录成功数/总数和恢复附加延迟；
4. 验证成功 Step/Tool 是否被重复执行；
5. 保存 RuntimeEvent 时间线和失败分类；
6. 修复至少一个暴露出的真实问题并固化 regression；
7. 更新 Compose smoke，确保清理和重跑可重复。

验收：

- [x] 所有 fault case 都能由自动化命令注入和复位；
- [x] Recovery Success Rate、E2E Success Rate、重复执行数和 P95 恢复延迟有明确分母；
- [x] WorkerCrash 至少跨两个独立 Worker 进程验证；
- [x] DuplicateRequest 与 DuplicateDelivery 不产生重复 Run 或重复成功 ToolExecution；
- [x] 失败 trial 保留，不从统计中删除；
- [x] 真实失败至少形成一条 executable regression。

本任务不实现：K8s、混沌平台、Redis Streams、Lease/Heartbeat；除非当前测试给出简单方案不可靠的证据并单独更新架构决定。

建议验证命令：

```bash
pytest -q tests/recovery tests/regression
python -m opspilot.evaluation.cli reliability --config benchmarks/configs/runtime_faults.yaml
```

标准工件：`artifacts/evaluations/<evaluation_id>/reliability.json` 与逐 trial 时间线。

实测（2026-08-12）：在项目独立 `.venv`、真实 PostgreSQL 10.19、Redis 7.2.7 与独立 Worker 子进程上运行 5 类故障 × 3 轮，共 15 trial；Recovery Success 15/15、E2E 15/15，132 条成功 ToolExecution 中重复执行 0，WorkerCrash 三轮 P95 恢复延迟 602.711 ms，P95 E2E 1571.484 ms。三组 WorkerCrash 的退出码均为 `[97, 0, 0]`，代表崩溃进程、恢复扫描进程和续跑进程。工件：`artifacts/evaluations/20260812T061631Z-runtime-faults-v1/`。实验暴露并修复了重复 HTTP 请求放大 Redis 消息的问题，回归为 `tests/regression/test_duplicate_request_queue.py`。

### S3-T2 `[PARTIAL]` 冻结评测、回归门禁与最终可复现交付

项目背景：用固定数据和完整工件证明 OpsPilot 的诊断质量与恢复能力，形成最终项目形态。

系统位置：

```text
business benchmark + runtime benchmark + regression + Compose
-> reports/demos -> README/简历证据
```

输入：Stage 1 数据集与 baseline、Stage 2 Runtime、S3-T1 fault suite。

输出：最终 dev/frozen-test 报告、对照实验、回归门禁、Docker Compose、README 四个 Demo 和证据索引。

当天一次性完成：

1. 先在 dev 完成最后修复并运行全部 regression；
2. 锁定 config/version 后只运行一次 frozen test；
3. 同集比较 DeepRCA baseline 与 OpsPilot hybrid；可选补充 LLM-only；
4. 聚合 RCA、工具、E2E、恢复和延迟指标，并展示置信区间或至少分子/分母；
5. 对所有退化 case 做失败分类，不在 frozen test 后继续调参；
6. 完成 Compose 一键启动、健康检查、数据库 migration 和 smoke；
7. README 展示正常 RCA、异步 Worker、Worker Crash Recovery、Evaluation 四个可复现 Demo；
8. 清理不再使用的 `deeprca` 重复实现或保留最薄兼容层，统一导入；
9. 只从真实 artifact 填写项目介绍数字。

验收：

- [x] Ruff、unit、contract、integration、recovery、evaluation、regression、smoke 全部通过；
- [x] frozen-test manifest 含 dataset/config/model/case 数/命令/时间；
- [x] baseline 与 hybrid 的 predictions 和 metrics 均可追溯；
- [x] 报告同时列出 Hit@1/3、Evidence Recall、Tool/E2E/Recovery Success、P95 和 Normal/Noise FPR；
- [ ] 四个 Demo 在全新 Compose 环境按 README 命令可复现；
- [x] README 明确 DeepRCA 复用能力与 OpsPilot 新增 Runtime/Evaluation；
- [x] 没有占位百分比、待填结果或未运行却声称成功的内容。

本任务不实现：自动修复、前端、K8s、MCP 和没有实验意义的附加基础设施。

建议验证命令：

```bash
ruff check src tests
pytest -q
docker compose up --build --abort-on-container-exit
python -m opspilot.evaluation.cli run --config benchmarks/configs/deeprca_baseline.yaml --split test
python -m opspilot.evaluation.cli run --config benchmarks/configs/opspilot_hybrid.yaml --split test
```

标准工件：最终两个 `artifacts/evaluations/<evaluation_id>/` 目录、恢复报告、README Demo 输出与证据索引。

实测（2026-08-12）：锁定数据和配置后只运行一次 frozen test。`rca-benchmark@1.0.0` test 为 12 case（10 fault、2 normal/noise）；baseline Hit@1/3=1/10、Evidence Recall=0.0、E2E=12/12、FPR=0/2，9 个错因 case 均保留；hybrid Hit@1/3=10/10、Evidence Recall=1.0、Tool Success=108/108、E2E=12/12、FPR=0/2。工件分别为 `20260812T052913Z-deeprca_baseline-test` 与 `20260812T052914Z-opspilot_hybrid-test`，聚合索引为 `artifacts/stage3/final_evidence.json`。已新增项目级 `.venv`（Python 3.11.7）并用 `scripts/bootstrap_dev_env.sh` 提供可复现安装；该隔离环境中 Ruff 通过，全量测试在真实 PostgreSQL/Redis 以及本地 API/Worker/Mock 三进程下为 248 passed（包含 3 个真实服务 integration 和 49 个 HTTP smoke）。Compose v5.4.0 的 profile/config 检查通过；官方 Rootless Docker 在线安装已实际尝试，但当前账号没有 sudo，且主机 `/etc/subuid`、`/etc/subgid` 为空、`ip_tables` 未加载，安装器无法创建 daemon。管理员完成 README 所列主机前置配置后可运行 `scripts/install_rootless_docker.sh`，因此“全新容器环境四 Demo”仍保持未完成，不虚报通过。

Stage 3 出口：所有验收项有真实命令和工件；没有证据的能力不写入最终项目介绍。

## 3. 阶段变更规则

只在以下证据出现时调整计划：

- 当前 DeepRCA 行为无法通过 characterization test 固定；
- 简单 Redis Queue 在重复投递/恢复测试中出现无法通过幂等解决的冲突；
- Checkpoint 序列化证明现有 LangGraph Step 边界不可恢复；
- 数据集标签一致性不足，导致指标无法解释；
- 真实实验显示主要瓶颈不在计划中的模块。

调整时直接修改受影响任务的输入、验收和工件，并记录证据；不新增一层泛化架构，也不把已知瓶颈无限移到 future work。

## 4. 每次任务完成汇报模板

1. 通过和未通过的验收项；
2. 修改文件、依赖、schema/migration；
3. `输入结构 -> 核心函数 -> 输出结构` 主调用链；
4. 2 至 4 个核心阅读点和一条主流程测试；
5. 实际测试/实验命令和真实结果；
6. dataset/version/split/config/case 数与指标；
7. report、trace、prediction、regression 工件路径；
8. 当前限制和明确未实现内容；
9. 3 至 5 个面试追问；
10. 紧邻的下一个完整任务。
