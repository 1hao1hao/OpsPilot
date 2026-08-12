可以，这次我就按**“最终完成态”**来设计，不再拆成简单版。

我先给结论：如果你要在 DeepRCA 基础上做出一个真正值得放在简历里的第二项目，我建议最终目标不是“DeepRCA + 几个功能”，而是把它重构成：

> **OpsPilot：面向微服务故障诊断的可恢复 Agent RCA 平台**
>
> 核心关键词：**LangGraph 多 Agent 编排 + Tool Calling + 确定性异常检测 + Evidence 推理 + Redis Streams Worker Pool + PostgreSQL Checkpoint + Lease/Heartbeat + Idempotency + Retry/DLQ + Trace + RCA Evaluation + Fault Injection**

DeepRCA 已经有一个不错的“业务 Agent 层”：Coordinator、DB/Redis/Kafka/RPC Expert、Root Cause Agent，主流程是 `intake → planner → dispatcher → collector → root_cause → reporter`，领域 Expert 目前主要通过 `asyncio.gather` 并发执行，并具备单 Expert 超时降级。 

你真正要做的，是在它下面补上一层**真正的 Agent Runtime / 执行系统**，在它上面补一层**系统化 Evaluation / Regression**。

---

# 一、最终项目一句话定义

以后你自己首先要能把 OpsPilot 讲成：

> OpsPilot 是一个面向微服务线上故障诊断的 Agent RCA 平台。系统接收告警后，由 Coordinator Agent 自动拆解诊断计划，并行调用指标、日志、变更、调用链、拓扑等工具和领域 Expert 获取证据，再通过异常检测、规则引擎和 LLM 融合推理定位根因。为了支持分钟级长任务和工具故障，我额外设计了持久化 Run/Step、Redis Streams Worker Pool、Checkpoint、Lease/Heartbeat、幂等执行、Retry/DLQ 和故障恢复机制，同时构建了可重复故障注入环境和 RCA Evaluation Benchmark。

这就是项目灵魂。

不是：

> 我做了一个 Multi-Agent。

而是：

> **我做了一个“能运行、能失败、能恢复、能评测”的 Agent 系统。**

---

# 二、最终整体架构

最终架构我建议直接做到这个形态：

```text
                         ┌──────────────────────┐
                         │     Alert Source     │
                         │ API / Webhook / Mock │
                         └──────────┬───────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────┐
│                       FastAPI Gateway                         │
│  Submit Run / Status / Result / Cancel / Trace / WebSocket   │
└──────────────────────────────┬────────────────────────────────┘
                               │
                     Idempotency Check
                               │
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                    Agent Runtime Service                      │
│                                                               │
│ Run Manager                                                   │
│ Step Manager                                                  │
│ Checkpoint Manager                                            │
│ Scheduler                                                     │
│ Retry Policy                                                  │
│ Timeout / Cancellation                                        │
│ Trace / Event Recorder                                        │
└───────────────┬───────────────────────────────┬───────────────┘
                │                               │
                ▼                               ▼
        PostgreSQL                      Redis Streams
     Durable Source of Truth          Task Delivery Layer
                │                               │
                │                   Consumer Group / PEL
                │                               │
                │                     ┌─────────┼─────────┐
                │                     ▼         ▼         ▼
                │                  Worker A  Worker B  Worker C
                │                     │         │         │
                └─────────────────────┴────┬────┴─────────┘
                                           │
                                           ▼
┌───────────────────────────────────────────────────────────────┐
│                 LangGraph Agent Orchestration                 │
│                                                               │
│ Intake                                                        │
│   ↓                                                           │
│ Planner                                                       │
│   ↓                                                           │
│ Parallel Dispatcher                                           │
│   ├── Change Analyzer                                         │
│   ├── Metrics Analyzer                                        │
│   ├── Log Analyzer                                            │
│   ├── Dependency Analyzer                                     │
│   ├── DB Expert                                               │
│   ├── Redis Expert                                            │
│   ├── Kafka Expert                                            │
│   └── RPC Expert                                              │
│            ↓                                                  │
│      Evidence Collector                                       │
│            ↓                                                  │
│ Deterministic RCA Engine                                      │
│   IQR / Volatility / WoW-DoD / Filters / Rules                │
│            ↓                                                  │
│ Root Cause Agent                                              │
│            ↓                                                  │
│ Reporter                                                      │
└───────────────────────────────────────────────────────────────┘
                            │
                            ▼
                ┌────────────────────┐
                │ Structured Report  │
                │ Root Cause         │
                │ Confidence         │
                │ Evidence Chain     │
                │ Repair Suggestion  │
                └────────────────────┘

旁路系统：

Trace / Metrics / Evaluation / Regression / Fault Injection
```

这和 DeepRCA 最大区别在于：

DeepRCA 现在更多是：

> **Agent 服务里运行 Agent**

而 OpsPilot 最终是：

> **Agent Runtime 调度和管理 Agent 执行。**

DeepRCA 当前 Compose 主要是 Redis、Agent Service、Mock Environment 和 Smoke Test 四类服务，并没有 PostgreSQL 持久化运行状态、独立 Worker Pool 或任务恢复服务。

这些正是你的主要新增部分。

---

# 三、最终的 Agent 层：保留 DeepRCA 三层思想，但重构职责

DeepRCA 目前已经有：

- L1 Coordinator Agent
- L2 DB / Redis / Kafka / RPC Expert
- L3 Root Cause Agent

而且总体设计就是让 Coordinator 做任务拆解和汇总，Expert 负责专项分析，Root Cause Agent 做最终融合推理。

这个框架我建议**保留**。

但是重新定义每一层。

---

## L1：Coordinator Agent

职责：

```text
Alert
 ↓
Normalize
 ↓
理解故障类型
 ↓
制定诊断计划
 ↓
选择分析维度
 ↓
决定调用哪些 Tool / Expert
 ↓
汇聚结果
```

例如告警：

```text
order-service
P99 latency 350ms → 1800ms
error rate 0.5% → 8%
```

Planner 输出：

```json
[
  {
    "dimension": "change",
    "priority": 1
  },
  {
    "dimension": "rpc",
    "priority": 1
  },
  {
    "dimension": "db",
    "priority": 2
  },
  {
    "dimension": "redis",
    "priority": 3
  }
]
```

不是每次所有 Agent 全跑。

这就形成：

> **dynamic planning**

---

# 四、L2 Domain Expert 最终设计

建议保留 4 个：

### DB Expert

调用：

```text
query_db_connections
query_slow_queries
query_lock_wait
query_replica_delay
query_db_cpu
```

分析：

- 连接池耗尽；
- 慢 SQL；
- 锁竞争；
- 主从延迟；
- DB CPU 饱和。

---

### Redis Expert

调用：

```text
query_memory
query_hit_rate
query_hot_keys
query_big_keys
query_eviction
query_latency
```

---

### Kafka Expert

调用：

```text
query_consumer_lag
query_produce_rate
query_consume_rate
query_rebalance
query_partition_skew
```

---

### RPC Expert

调用：

```text
query_trace
query_dependency_graph
query_upstream_qps
query_downstream_latency
query_error_rate
```

---

这里要特别注意：

Expert Agent 不应该直接：

> “感觉数据库有问题。”

而是必须输出标准对象：

```text
SubAgentResult

agent_name
dimension
status

findings[]
evidence[]
confidence

tool_calls[]
latency_ms
error
```

---

# 五、统一 Tool Registry

这是 DeepRCA 可以进一步工程化的地方。

最终做：

```text
ToolRegistry
    │
    ├── metrics.query
    ├── logs.query
    ├── changes.query
    ├── trace.query
    ├── topology.query
    ├── db.query_slow_log
    ├── redis.query_hotkey
    └── kafka.query_lag
```

每个 Tool 注册：

```text
tool_name
version
description

input_schema
output_schema

timeout
retry_policy
side_effect

rate_limit

idempotent
```

例如：

```text
query_metrics

side_effect = false
timeout = 5s
retry = 2
idempotent = true
```

如果未来加入：

```text
restart_service
rollback_deployment
```

则：

```text
side_effect = true
approval_required = true
```

不过 OpsPilot 最终版本**可以只做到 diagnosis，不自动执行修复**。

这样安全边界更清晰。

---

# 六、工具统一执行器 ToolExecutor

不要让每个 Agent 自己写：

```python
try:
    tool(...)
except:
    ...
```

统一：

```text
ToolExecutor.execute()
```

负责：

```text
Schema Validation
        ↓
Permission Check
        ↓
Idempotency Check
        ↓
Rate Limit
        ↓
Timeout
        ↓
Execute
        ↓
Retry
        ↓
Record ToolExecution
        ↓
Return Structured Result
```

这是一个非常好的 Agent Infra 亮点。

---

# 七、Evidence Pool

这个一定做成整个项目核心数据结构。

所有 Agent 和 Tool 都不直接“投票根因”。

先产 Evidence：

```text
Evidence

evidence_id

source_type
    metric
    log
    trace
    change
    topology
    rule

source

timestamp

service

content

severity

confidence

dimension

supporting_root_cause
```

例如：

```text
E1:
db_connection_usage = 97%

E2:
slow_query_count increased 8x

E3:
deployment version changed 15 min before alert

E4:
order-service -> payment-service P99 normal

E5:
redis latency normal
```

Collector 汇总：

```text
EvidencePool
```

之后才能进入 Root Cause Engine。

---

# 八、Deterministic RCA Engine

这个是 DeepRCA 最值得保留的部分。

它已经实现了 IQR 四分位异常检测，并根据历史 baseline、当前值、IQR 区间和偏离倍数判断 spike/drop/level_shift 以及 severity/confidence。

最终 OpsPilot 做五层处理。

---

## 1. Metric Filter

先筛：

```text
QPS
error_rate
P95
P99
CPU
memory
connection_pool
consumer_lag
hit_rate
```

避免几百个指标全部给 LLM。

---

# 九、IQR Anomaly Detector

保留：

```text
Q1
Q3
IQR

lower = Q1 - 1.5*IQR
upper = Q3 + 1.5*IQR
```

判断当前点是否超出历史分布。

用途：

> 找 spike / drop。

---

# 十、Volatility Detector

计算：

```text
rolling std
```

比较：

```text
historical volatility
vs
current volatility
```

检测：

> 均值没有明显变化，但抖动突然增大。

---

# 十一、WoW + DoD Comparator

保留 DeepRCA 的：

```text
当前时段
vs
一天前
vs
一周前
```

这样避免：

> 每天中午 QPS 本来就高

被误判为故障。

DeepRCA 已经把多维对比、异常检测、证据排序、专家规则和最终 LLM 推理串成完整 RCA 流程。

---

# 十二、Noise Filter

新增：

过滤：

```text
短时抖动
低流量服务
无业务影响指标
重复异常
低置信度 Evidence
```

---

# 十三、Expert Rule Engine

继续保留规则，但不要写死到 Agent Prompt。

设计：

```text
Rule

rule_id
name

conditions[]

action

confidence_boost

explanation
```

例如：

```text
R_DB_001

IF

db_connection_usage > 95%
AND
request_latency ↑
AND
downstream_rpc_normal

THEN

candidate = DB_CONNECTION_POOL_EXHAUSTED
confidence += 0.25
```

---

# 十四、Evidence Ranking

最终给 LLM 之前：

```text
evidence_score =
reliability_weight
× severity
× temporal_relevance
× causal_relevance
× confidence
```

取 Top-N。

这样你以后面试可以说：

> 我没有把所有监控数据直接塞给 LLM，而是先通过传统算法和规则完成筛选和排序，再让 LLM 做高层因果综合。

这是非常好的回答。

---

# 十五、Root Cause Agent

输入：

```text
Alert
+
Analysis Plan
+
Top Evidence
+
Rule Candidates
+
Topology
+
Historical Incident（可选）
```

输出严格结构化：

```text
RootCauseResult

primary_root_cause

root_cause_type

affected_component

confidence

supporting_evidence[]

contradicting_evidence[]

alternative_causes[]

reasoning_summary

recommended_actions[]
```

注意：

不要保存完整 Chain-of-Thought。

保存：

> reasoning summary / decision rationale

即可。

---

# 十六、最核心新增：Agent Runtime

现在进入项目真正区别 DeepRCA 的地方。

你最终不是直接：

```text
POST /analyze
↓
await graph.invoke()
```

而是：

```text
POST /runs
↓
创建 Run
↓
入队
↓
Worker claim
↓
从 checkpoint 执行 graph
```

---

# 十七、Run 数据模型

一次完整 RCA：

```text
Run

run_id
alert_id

status

priority

current_step

created_at
started_at
finished_at

owner_worker

lease_expire_at

retry_count

error_code
error_message

graph_version
config_version

token_usage
total_latency
```

状态：

```text
QUEUED

RUNNING

WAITING_TOOL

RETRYING

RECOVERING

SUCCEEDED

FAILED

CANCELLED

DEAD_LETTER
```

---

# 十八、Step 数据模型

每个 Agent Graph Node 一个 Step：

```text
Step

step_id
run_id

step_type
step_name

status

attempt

worker_id

input_ref
output_ref

started_at
finished_at

error
```

例如：

```text
planner
db_expert
rpc_expert
evidence_collect
root_cause
reporter
```

---

# 十九、Checkpoint

这是项目第二大核心。

每个关键 Node 完成后：

```text
Agent State
    ↓
Serialize
    ↓
Checkpoint
```

表：

```text
checkpoints

checkpoint_id
run_id
step_id

graph_node

state_json

version

created_at
```

例如 Worker 在：

```text
planner
↓
db analysis
↓
redis analysis
↓
CRASH
```

Redis Worker 重新领取后：

```text
找到最新 checkpoint
↓
恢复 state
↓
从 redis analysis 后继续
```

不是：

> 从 alert intake 重新跑。

---

# 二十、为什么 PostgreSQL + Redis 都需要

这一定要理解。

### PostgreSQL

**Source of Truth**

保存：

- Run；
- Step；
- Checkpoint；
- ToolExecution；
- Evidence；
- Report；
- Evaluation。

因为必须持久。

---

### Redis

**Coordination / Delivery**

负责：

- Task Queue；
- Consumer Group；
- Worker lease；
- heartbeat；
- rate limit；
- transient cache。

Redis 挂了重启：

> PostgreSQL 里的任务状态还在。

这个设计比“所有状态都存在 Redis”成熟很多。

DeepRCA 当前 Compose 中 Redis 同时承担缓存/状态角色；OpsPilot 则要明确区分持久状态和调度状态。

---

# 二十一、队列最终直接用 Redis Streams

我建议别再写简单 List Queue。

用：

> Redis Streams + Consumer Group

结构：

```text
ops:run:queue
```

Worker：

```text
consumer group = rca-workers
```

Worker A：

```text
XREADGROUP
```

领取：

```text
run_001
```

任务进入：

> Pending Entries List

完成：

```text
XACK
```

---

# 二十二、Worker Pool

Docker Compose：

```text
worker-1
worker-2
worker-3
```

每个 Worker：

```text
worker_id

hostname

started_at

last_heartbeat

current_run

status
```

---

# 二十三、Heartbeat + Lease

Worker 执行任务时：

```text
每 5 秒 heartbeat
```

Run：

```text
owner_worker = worker_1

lease_expire_at =
now + 15s
```

正常：

```text
Worker1
 ↓
heartbeat
 ↓
extend lease
```

突然：

```text
kill -9 worker1
```

则：

```text
heartbeat停止
↓
lease过期
↓
Recovery Scheduler
↓
Worker2 claim
↓
读取checkpoint
↓
继续
```

这个是你 README 必须做 GIF / Demo 的东西之一。

---

# 二十四、Delivery Semantics

不要说：

> exactly once。

应该设计成：

> **at-least-once delivery + idempotent execution**

这是非常重要的工程点。

因为任务有可能：

```text
Worker执行成功
↓
还没ACK
↓
Worker崩溃
```

任务被重新领取。

所以：

> 同一个 Step 有可能运行两次。

解决方式：

> Idempotency。

---

# 二十五、幂等设计

三层。

### API 幂等

客户端：

```text
Idempotency-Key:
alert:prod:order:abc123
```

数据库唯一约束：

```text
UNIQUE(idempotency_key)
```

重复：

```text
POST /runs
```

返回已有：

```text
run_id
```

---

### Step 幂等

唯一键：

```text
run_id + step_name + execution_version
```

如果：

```text
step already succeeded
```

恢复时：

> 直接复用 output。

---

### Tool 幂等

```text
tool_call_id =
hash(run_id + step_id + tool_name + normalized_args)
```

查询类工具天然幂等。

如果未来有：

```text
send_alert
create_ticket
```

先检查：

```text
tool_call_id
```

是否执行过。

---

# 二十六、Retry

错误必须分类。

```text
RetryableError

LLMTimeout
ToolTimeout
HTTP503
RateLimit
RedisTemporaryError
```

允许 retry。

---

```text
PermanentError

InvalidSchema
PermissionDenied
InvalidAlert
UnknownTool
```

不 retry。

策略：

```text
attempt 1
↓
1s

attempt 2
↓
2s

attempt 3
↓
4s
```

Exponential Backoff + Jitter。

---

# 二十七、Dead Letter Queue

超过：

```text
max_attempts
```

进入：

```text
ops:run:dlq
```

同时：

```text
Run.status = DEAD_LETTER
```

保存：

```text
failure_reason
last_checkpoint
last_error
attempts
```

提供：

```text
POST /runs/{id}/replay
```

进行人工重放。

这个才是 DLQ 真正意义。

---

# 二十八、Timeout

三个层级：

### Tool timeout

例如：

```text
5s
```

---

### Step timeout

例如：

```text
20s
```

---

### Run timeout

例如：

```text
60s / 120s
```

超过总预算：

Coordinator 可以：

```text
停止低优先级 Agent
↓
使用已有 Evidence
↓
生成 degraded report
```

而不是：

> 直接全部失败。

这个可以叫：

> graceful degradation。

DeepRCA 已经存在 dispatcher 超时后跳过 collector 直接进入 root cause 的条件路径，因此你是在它已有超时降级思想上进一步工程化。

---

# 二十九、Cancellation

API：

```text
DELETE /runs/{run_id}
```

或者：

```text
POST /runs/{run_id}/cancel
```

设置：

```text
cancel_requested = true
```

Worker 在：

```text
step boundary
tool boundary
```

检查。

然后：

```text
CANCELLED
```

---

# 三十、并发控制

不是无限：

```text
asyncio.gather()
```

最终有两层。

### Run-level

最多：

```text
N active runs
```

---

### Tool-level

每 Tool：

```text
LLM concurrency = 5

metrics concurrency = 20

logs concurrency = 10
```

Redis Semaphore / Token Bucket。

避免：

> 100 个 Agent 一起打 LLM API。

---

# 三十一、Priority

P0/P1/P2/P3 告警。

最终支持：

```text
priority score
```

例如：

```text
P0 = 100
P1 = 80
P2 = 50
P3 = 20
```

调度优先级。

这对 RCA 场景很自然。

---

# 三十二、完整 Trace

你的 EvalRAG Trace 思想直接迁移过来。

结构：

```text
RunTrace
 │
 ├── StepTrace
 │     │
 │     ├── AgentTrace
 │     │
 │     └── ToolTrace
 │
 └── RuntimeEvent
```

---

### Run Trace

记录：

```text
run_id
status
total latency
tokens
retry count
recovery count
```

---

### Step Trace

```text
planner
db_expert
root_cause
```

---

### Tool Trace

```text
tool_name
args_digest
duration
result status
retry
error
```

---

### Runtime Event

例如：

```text
RUN_CREATED

TASK_ENQUEUED

WORKER_CLAIMED

STEP_STARTED

CHECKPOINT_SAVED

TOOL_TIMEOUT

STEP_RETRY

WORKER_HEARTBEAT_LOST

LEASE_EXPIRED

RUN_RECOVERED

RUN_SUCCEEDED
```

这部分会非常漂亮。

---

# 三十三、WebSocket 实时进度

DeepRCA 已经有 WebSocket 进度接口，这个可以保留。

最终推：

```text
10% Intake complete

25% Planning complete

40% DB analysis complete

55% RPC analysis complete

70% Evidence collected

85% Root cause inferred

100% Report generated
```

如果恢复：

```text
Worker failure detected

Recovering from checkpoint...

Resumed on worker-2
```

展示效果很好。

---

# 三十四、最终 PostgreSQL Schema

至少这些表：

```text
runs

steps

checkpoints

tool_executions

evidence

diagnosis_reports

runtime_events

workers

idempotency_keys
```

Evaluation：

```text
evaluation_datasets

evaluation_cases

evaluation_runs

evaluation_results

regression_cases
```

---

# 三十五、Mock Environment 不只是 Demo，要变 Benchmark

DeepRCA 已经有：

- K8s simulator
- MySQL simulator
- Redis simulator
- Kafka simulator
- Service simulator
- Alert simulator 

你最终把它升级成：

> **Fault Scenario Registry**

每个场景：

```text
scenario_id

services

injected_fault

ground_truth_root_cause

expected_evidence

distractor_evidence

severity

duration
```

---

# 三十六、至少设计 30～50 个 RCA 场景

例如：

### DB

```text
DB_CONNECTION_EXHAUSTED

DB_SLOW_QUERY

DB_LOCK_CONTENTION

DB_REPLICA_LAG
```

### Redis

```text
REDIS_HOT_KEY

REDIS_BIG_KEY

REDIS_MEMORY_PRESSURE

REDIS_LOW_HIT_RATE
```

### Kafka

```text
CONSUMER_LAG

PARTITION_SKEW

REBALANCE_STORM
```

### RPC

```text
DOWNSTREAM_TIMEOUT

ERROR_RATE_SPIKE

DEPENDENCY_FAILURE
```

### Deploy

```text
BAD_DEPLOYMENT

CONFIG_CHANGE

VERSION_REGRESSION
```

### Normal / Noise

这类特别重要：

```text
NORMAL_TRAFFIC_PEAK

SHORT_METRIC_SPIKE

NON_CAUSAL_WARNING
```

测试误报。

---

# 三十七、同时做 Runtime Fault Injection

这和业务故障不同。

业务故障：

> 系统正在诊断什么问题。

Runtime 故障：

> Agent 自己执行过程中出什么问题。

至少：

```text
WorkerCrash

LLMTimeout

LLMRateLimit

ToolTimeout

ToolHTTP500

RedisRestart

PostgresTemporaryFailure

DuplicateRequest

DuplicateDelivery

MalformedToolResult
```

---

# 三十八、最终 Evaluation 架构

这是你相比 DeepRCA 必须做强的地方。

DeepRCA PRD 提出了根因命中率、关键线索命中率和端到端时延目标，但 OpsPilot 要把它真正做成 frozen benchmark + regression system。

最终分四层评测。

---

## A. Planning

### Plan Accuracy

真实需要：

```text
DB + RPC
```

Agent 是否选择正确。

可以：

```text
Precision
Recall
F1
```

---

# 三十九、Tool Evaluation

### Tool Selection Accuracy

是否调用正确工具。

### Tool Success Rate

```text
successful / attempted
```

### Redundant Tool Call Rate

无意义调用比例。

### Tool Retry Rate

工具稳定性。

---

# 四十、Evidence Evaluation

### Evidence Recall

真实根因所需 Evidence：

```text
E1 E2 E3
```

系统找到：

```text
E1 E3
```

Recall：

```text
2/3
```

---

### Evidence Precision

系统拿了一堆无关 evidence 也不好。

---

# 四十一、Root Cause Evaluation

最核心：

### Root Cause Hit@1

第一名是不是正确。

### Hit@3

Top3 是否包含。

### MRR

正确根因排名。

可以自然复用你 EvalRAG 已经熟悉的思想。

---

# 四十二、Reliability Evaluation

### E2E Success Rate

完整完成诊断比例。

### Recovery Success Rate

Runtime 故障后是否恢复。

### Duplicate Side Effect Rate

应该：

```text
0
```

### DLQ Rate

多少任务最终失败。

---

# 四十三、Performance

记录：

```text
P50 latency
P95 latency

Planner latency

Tool latency

LLM latency

Recovery latency

Token usage

Cost / Run
```

---

# 四十四、最关键的消融实验

一定至少跑下面四组。

### Experiment A

```text
LLM Only
```

把原始指标/日志直接给 LLM。

---

### Experiment B

```text
LLM + Tools
```

Agent 自主调用工具。

---

### Experiment C

```text
Tools + Deterministic Algorithm + Rules + LLM
```

你的完整版。

测：

```text
Hit@1
Evidence Recall
False Positive
Tool Calls
Latency
Token
```

---

### Experiment D

Runtime Reliability

```text
No Recovery

vs

Checkpoint + Retry

vs

Checkpoint + Lease + Idempotency
```

测：

```text
Recovery Success Rate

Duplicate Execution Rate

Additional Recovery Latency
```

这个实验对简历尤其漂亮。

---

# 四十五、Regression

每次失败：

```text
Failure
 ↓
Root cause analysis
 ↓
fix
 ↓
Regression Case
```

Regression Case：

```text
case_id

scenario

expected_root_cause

expected_evidence

expected_tools

previous_failure_type

fixed_version
```

每次：

```text
Agent Prompt
Rule
Planner
Algorithm
Runtime
```

变化以后自动跑。

这正好把你 EvalRAG 最成熟的方法迁移过去。

---

# 四十六、最终 API

至少：

```text
POST /api/v1/runs

GET /api/v1/runs/{run_id}

GET /api/v1/runs/{run_id}/result

GET /api/v1/runs/{run_id}/trace

POST /api/v1/runs/{run_id}/cancel

POST /api/v1/runs/{run_id}/retry

POST /api/v1/runs/{run_id}/replay

WS /api/v1/runs/{run_id}/stream
```

---

Evaluation：

```text
POST /api/v1/evaluations

GET /api/v1/evaluations/{id}

GET /api/v1/evaluations/{id}/report
```

---

Mock：

```text
POST /api/v1/scenarios/{id}/inject

POST /api/v1/runtime-faults/worker-crash

GET /api/v1/scenarios
```

---

# 四十七、最终 Docker Compose

不要只有一个 Agent 容器。

最终：

```text
api

scheduler

worker-1

worker-2

worker-3

postgres

redis

mock-env

evaluation-worker
```

可选：

```text
prometheus
grafana
```

但我认为 Grafana **不是简历核心，不做也没关系**。

---

# 四十八、最终目录结构

建议：

```text
opspilot/
│
├── src/opspilot/
│
│   ├── api/
│   │   ├── routes/
│   │   ├── websocket.py
│   │   └── schemas.py
│
│   ├── runtime/
│   │   ├── run_manager.py
│   │   ├── step_manager.py
│   │   ├── scheduler.py
│   │   ├── worker.py
│   │   ├── lease.py
│   │   ├── heartbeat.py
│   │   ├── checkpoint.py
│   │   ├── retry.py
│   │   ├── idempotency.py
│   │   ├── cancellation.py
│   │   └── recovery.py
│
│   ├── graph/
│   │   ├── state.py
│   │   ├── coordinator.py
│   │   └── experts/
│
│   ├── agents/
│   │   ├── coordinator.py
│   │   ├── db_expert.py
│   │   ├── redis_expert.py
│   │   ├── kafka_expert.py
│   │   ├── rpc_expert.py
│   │   └── root_cause.py
│
│   ├── tools/
│   │   ├── registry.py
│   │   ├── executor.py
│   │   ├── metrics.py
│   │   ├── logs.py
│   │   ├── change.py
│   │   ├── trace.py
│   │   └── topology.py
│
│   ├── rca/
│   │   ├── quantile.py
│   │   ├── volatility.py
│   │   ├── comparator.py
│   │   ├── noise_filter.py
│   │   ├── evidence_ranker.py
│   │   └── rule_engine.py
│
│   ├── evidence/
│   │   ├── models.py
│   │   ├── pool.py
│   │   └── collector.py
│
│   ├── persistence/
│   │   ├── postgres.py
│   │   ├── repositories/
│   │   └── models.py
│
│   ├── queue/
│   │   ├── redis_stream.py
│   │   └── consumer_group.py
│
│   ├── tracing/
│   │   ├── trace.py
│   │   └── events.py
│
│   ├── evaluation/
│   │   ├── dataset.py
│   │   ├── runner.py
│   │   ├── metrics.py
│   │   ├── ablation.py
│   │   ├── regression.py
│   │   └── report.py
│
│   └── config/
│
├── mock_env/
│   ├── scenarios/
│   ├── mysql/
│   ├── redis/
│   ├── kafka/
│   ├── services/
│   └── fault_injector/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── recovery/
│   ├── evaluation/
│   └── smoke/
│
├── reports/
│
├── docker-compose.yml
└── README.md
```

---

# 四十九、最终技术栈

如果真的按这个设计做完，技术栈可以堂堂正正写：

> **Python、FastAPI、LangGraph、PostgreSQL、Redis Streams、Docker Compose、SQLAlchemy、asyncio、LLM API**

可以增加：

> Pydantic

但是没必要全塞简历。

---

# 五十、DeepRCA → OpsPilot 到底新增哪些核心东西

最重要的变化总结：

| DeepRCA | OpsPilot 最终版 |
|---|---|
| LangGraph Agent | 保留 |
| 三层 Agent | 保留并完善 |
| Expert Registry | Tool/Agent Registry |
| asyncio 并发 | **Redis Streams Worker Pool** |
| Redis 状态 | **PostgreSQL Durable State + Redis Coordination** |
| 简单超时降级 | **Retry + Backoff + DLQ** |
| trace_id | **Run/Step/Agent/Tool Trace** |
| 无完整任务恢复 | **Checkpoint + Resume** |
| 无 Worker ownership | **Lease + Heartbeat** |
| 无严格幂等 | **API/Step/Tool 三层 Idempotency** |
| Mock Env | **Benchmark + Fault Injection** |
| RCA 算法 | 保留并消融 |
| Evaluation目标 | **Frozen Benchmark + 多层指标 + Regression** |
| 单 Agent 服务 | **API/Scheduler/Worker/DB/Queue 多服务架构** |

所以真正需要你写的新东西主要是：

> **Runtime + Reliability + Evaluation**

而不是继续堆 Agent 数量。

---

# 五十一、最终 README 必须展示 4 个 Demo

这部分非常重要。

### Demo 1：正常 RCA

```text
注入 DB connection exhaustion

↓

Agent诊断

↓

输出：

Root Cause:
DB connection pool exhausted

Confidence:
0.91

Evidence:
connection usage 98%
slow query ↑
RPC downstream normal
```

---

### Demo 2：并行分析

展示：

```text
DB Expert
Redis Expert
RPC Expert
Kafka Expert
```

并行完成。

对比串行 latency。

---

### Demo 3：Worker Crash Recovery

这是王牌 Demo。

```text
Run started on worker-1

↓

kill worker-1

↓

heartbeat lost

↓

lease expired

↓

worker-2 claimed

↓

resume checkpoint

↓

Run succeeded
```

README 给出 Trace。

---

### Demo 4：Evaluation

显示：

```text
Algorithm                  Hit@1
--------------------------------
LLM Only                   xx
LLM + Tools                xx
Hybrid RCA                 xx
```

以及：

```text
Recovery Success Rate
P95
Tool Calls
Token
```

---

# 五十二、最后对应到你的最终简历

当上述东西真的完成、指标真实跑出来后，项目介绍就可以收敛为大约这样：

**OpsPilot：面向微服务故障诊断的可恢复 Agent RCA 平台｜个人项目**

**技术栈：** Python、LangGraph、FastAPI、PostgreSQL、Redis Streams、Docker Compose、LLM API

- **Agent RCA 架构：** 面向微服务告警与故障定位场景，基于 LangGraph 构建 Coordinator → Domain Expert → Evidence Collector → Root Cause Agent 多阶段诊断链路，封装 Metrics、Logs、Changes、Trace、Topology 等工具；结合 IQR 异常检测、波动检测、同比/环比分析和专家规则完成 Evidence 筛选与排序，再由 LLM 融合推理输出带置信度与证据链的根因报告。
- **可恢复 Agent Runtime：** 将诊断任务抽象为持久化 Run/Step，使用 PostgreSQL 保存运行状态与 Checkpoint、Redis Streams Consumer Group 调度多 Worker；实现 Lease/Heartbeat、超时重试、指数退避、Dead Letter Queue 及 API/Step/Tool 三级幂等，在 Worker Crash、服务重启及工具超时等故障注入场景下实现 `[X%]` 任务恢复成功率与 `0` 次重复副作用执行。
- **RCA Evaluation：** 构建 `[N]` 个可重复微服务故障场景与人工标注 Evidence Ground Truth，对比 LLM-only、Tool-Augmented 和 Algorithm+Rule+LLM 三种方案；完整方案将 Root-Cause Hit@1 从 `[X%]` 提升至 `[Y%]`、Evidence Recall 从 `[A%]` 提升至 `[B%]`，同时降低 `[C%]` 无效 Tool Call / Token 开销。
- **可观测与回归：** 建立 Run → Step → Agent → Tool 四级 Trace，记录 Worker Ownership、Checkpoint、Retry、Recovery、Evidence、Latency 和 Token；将诊断失败与 Runtime 故障沉淀为 Regression Case，并通过 Docker Compose 编排 API、Scheduler、Worker Pool、PostgreSQL、Redis 与 Mock Services，自动验证告警 → 分析 → 故障恢复 → 报告完整链路。

这就是我认为值得作为你**第二项目最终目标**的形态。

