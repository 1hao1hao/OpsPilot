下面这份可以直接丢给 Codex。目标是**只重构 Planner / L2 / Evidence 调查链，不破坏现有 L3、Runtime 和 Evaluation 基础设施**。

---

# OpsPilot Adaptive RCA 改造需求书

## 1. 改造目标

当前存在两个核心问题：

```text
1. Coordinator 对所有告警都规划六个维度，只调整顺序
2. DB / Redis / Kafka / RPC 四个 L2 Expert 基本全部执行，且只分析已有 ToolResult
```

当前实现可见 `CoordinatorAgent.plan()` 与 `analyze_experts()`。

重构目标：

```text
固定一次性 Planner
        ↓
Adaptive Investigation Loop

Plan
→ Execute
→ Observe
→ Re-plan
→ Dynamic Expert
→ Evidence Gate
→ Finalize
```

让 Agent **根据已经观察到的 Finding / Evidence 动态决定下一步查什么**，而不是提前把所有 Tool 和 Expert 跑完。

---

# 2. 改造后的主链

```text
Alert
 ↓
Seed Planner
 ↓
第一轮低成本 Tool
 ↓
Incremental L1 Analysis
 ↓
Finding / Evidence
 ↓
Adaptive Planner
 ├─ inspect_tool
 ├─ invoke_expert
 └─ finalize
        │
        ↓
Dynamic L2 Expert
 ↓
领域 Tool 下钻
 ↓
Expert Finding
 ↓
Expert Evidence
 ↓
Evidence Gate
 ├─ evidence insufficient → Re-plan
 └─ evidence sufficient / budget exhausted
                    ↓
                    L3
 MetricFilter / NoiseFilter
 WoW / DoD / IQR / Volatility
 ExpertRuleEngine
                    ↓
              Evidence Pool
                    ↓
          RootCause Ranking
                    ↓
                 Top-K
                    ↓
      Optional constrained LLM explanation
```

---

# 3. Seed Planner

不要一开始规划全部六维 Tool。

根据 `alert_type` 只选择 **2~3 个高信息量、低成本初始 Tool**。

例如：

```text
timeout
→ metrics.query
→ traces.query
→ changes.query

error_rate
→ metrics.query
→ logs.query
→ traces.query

resource
→ metrics.query
→ logs.query
```

六维概念继续保留，但从：

```text
六维全部执行
```

改成：

```text
六维候选调查空间
```

---

# 4. Adaptive Planner

新增 Adaptive Planner。

输入：

```text
Alert
当前已完成 actions
已有 ToolResults
L1 Findings
L2 Findings
当前 Evidence 摘要
当前 provisional Top-K
remaining_tool_budget
remaining_round_budget
```

输出必须是结构化 Action：

```text
inspect_tool(tool_name)

invoke_expert(domain)

finalize(reason)
```

例如：

```json
{
  "action": "invoke_expert",
  "target": "db",
  "reason": "Trace anomaly terminates at mysql and latency increased"
}
```

### Planner 原则

Planner 负责：

> **决定下一步调查动作。**

Planner 不负责：

```text
直接预测最终 root cause
修改 Evidence confidence
修改 RootCause ranking
```

可以使用 DeepSeek 做 Planner decision，但必须：

```text
allowed_actions 白名单
结构化输出
temperature=0
非法输出 deterministic fallback
```

---

# 5. Dynamic L2 Expert

废弃当前：

```python
analyze_experts(alert, all_results)
```

一次执行全部四个 Expert 的方式。

改为：

```text
invoke_expert("db")
invoke_expert("redis")
invoke_expert("kafka")
invoke_expert("rpc")
```

按 Planner 决策执行。

## DB Expert 示例

不要只读取一个 `db.inspect`。

建议拆成：

```text
db.metrics
db.slowlog
db.replication
db.connections
```

DB Expert 根据已有上下文决定需要哪些领域 Tool，再输出：

```text
db_replication_lag
db_slow_query
db_connection_exhausted
```

Redis / Kafka / RPC 同理。

已有 Mock Environment 已提供 DB slow-log、Redis hotkey、Kafka lag 等接口，应优先复用，不重新造整套环境。

---

# 6. L2 Finding 正式进入 Evidence

当前 L2 Finding 不直接走 semantic Evidence。

新增：

```python
collect_expert_evidence(...)
```

例如：

```text
db_replication_lag
→ Evidence
supports = DB_REPLICATION_LAG

redis_memory_pressure
→ Evidence
supports = REDIS_MEMORY_PRESSURE

kafka_consumer_lag
→ Evidence
supports = KAFKA_CONSUMER_LAG

rpc_timeout
→ Evidence
supports = RPC_TIMEOUT
```

形成：

```text
Tool Evidence
+ L1 Evidence
+ L2 Expert Evidence
+ L3 Algorithm Evidence
+ Rule Evidence
        ↓
Evidence Pool
```

---

# 7. Evidence Gate

每轮调查结束后进行一次 provisional ranking。

判断是否继续调查。

初始规则建议：

```text
满足以下条件可以 finalize：

Top1 confidence >= 0.8
AND
Top1 与 Top2 score margin 达到配置阈值
AND
Top1 至少有 2 个独立 evidence source 支持
```

否则：

```text
→ Re-plan
```

同时必须有硬终止条件：

```text
max_rounds
max_tool_calls
max_expert_calls
no_duplicate_action
```

达到 budget：

```text
→ 强制进入 L3 / Ranking
```

避免无限 Agent Loop。

---

# 8. Action 去重

维护：

```text
action_history
```

Action identity 至少包含：

```text
action_type
target
arguments
```

如果：

```text
同一个 Tool
+ 相同 arguments
```

已经成功执行，则 Planner 不允许再次调用。

这和现有 `tool_call_id` 持久化幂等互补：

```text
Planner层
→ 防止产生无意义重复 Action

Runtime层
→ 即使产生重复，也防止重复执行副作用
```

---

# 9. Runtime 集成

**不要推翻现有 Recoverable Runtime。**

需要把 adaptive loop 状态加入 Checkpoint，例如：

```text
investigation_round
action_history
executed_tools
invoked_experts
dimension_results
expert_results
evidence
provisional_candidates
remaining_budget
```

恢复后：

```text
load checkpoint
→ 恢复 Adaptive Planner 上下文
→ 从下一轮 Action 继续
```

现有：

```text
request_id
run_id
tool_call_id
ToolExecution
Checkpoint
stale recovery
```

全部保留。

---

# 10. L3 不做大改

现有：

```text
MetricFilter
NoiseFilter
WoW / DoD
IQR
Volatility
ExpertRuleEngine
RootCause Ranking
```

继续保留。

但 L3 应在：

```text
Evidence Gate finalize
或
budget exhausted
```

后集中执行。

不要让 LLM Planner修改 L3 的计算结果。

---

# 11. LLM 边界

明确区分两个 LLM 用途：

```text
Adaptive Planner LLM
→ 决定“下一步调查什么”

Explanation LLM
→ 最终解释确定性 Top1
```

两者都不能直接覆盖：

```text
Evidence
RootCause score
最终 deterministic ranking
```

如果 Planner LLM 不可用：

```text
→ deterministic planner fallback
```

整个 RCA 仍必须能够执行。

---

# 12. Evaluation 必须同步重构

不要再把：

```text
LLM Only / LLM + Tools / Hybrid
```

作为主要亮点实验。

新增核心消融：

```text
A. Fixed Planner
   六维 + Expert 全跑

B. Adaptive Planner
   动态 Tool / Expert

C. Adaptive Planner without Dynamic L2

D. Full Adaptive RCA
```

重点比较：

```text
Hit@1
Hit@3
Evidence Recall
FPR

平均 Tool Calls / Case
平均 Expert Calls / Case
平均 Investigation Rounds
P95 latency
```

最关键希望验证两个问题：

```text
① Adaptive 后准确率不能下降

② 在相同/更高准确率下，
   Tool Calls / Expert Calls 明显减少
```

再增加：

```text
Planner Action Valid Rate
Duplicate Action Rate
Budget Exhaustion Rate
```

---

# 13. 必须覆盖的测试场景

至少覆盖：

```text
DB replication lag
DB connection exhaustion
Redis memory pressure
Kafka consumer lag
RPC timeout
OOM
Traffic/resource saturation
Normal / noise
```

并额外设计几类“需要二次调查”的 case，例如：

```text
Alert只表现为 timeout

第一轮：
metrics → tp99异常
trace → mysql慢

第二轮：
Planner → DB Expert

第三轮：
DB replication → lag 15s

最终：
DB_REPLICATION_LAG
```

必须证明：

> **后续 Tool 是由前一轮 Observation 触发的，而不是预先写死全部执行。**

---

# 14. 验收标准

重构完成后必须能够真实展示：

```text
Round 1
Alert(timeout)
→ metrics.query
→ traces.query

Observation:
mysql span slow

Round 2
Adaptive Planner
→ invoke DB Expert

DB Expert
→ db.replication
→ db.slowlog

Observation:
replication lag = 15s

Evidence Gate
→ evidence sufficient

L3
→ Evidence fusion

RootCause Ranking
→ DB_REPLICATION_LAG
```

并能在 RuntimeEvent / Report 中看到：

```text
为什么执行某个 Action
哪一轮执行
产生了什么 Finding
为什么继续调查
为什么最终停止
```

---

## 最终目标

项目从：

```text
六维固定 Tool 流水线
+ 四个规则 Expert
```

升级为：

> **六维先验驱动的自适应 Agent RCA：Controller 根据实时 Observation 动态 Re-plan，并按需调度 Domain Expert 下钻，在 Evidence Gate 控制下完成多轮调查，最终由确定性算法融合多源 Evidence 输出根因。**

同时保留现有：

```text
L3核心算法
Evidence体系
Trace分析
Mock Environment
Recoverable Runtime
Fault Injection
```

避免无意义的大重写。
