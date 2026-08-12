
---

# OpsPilot 最终定位

一句话：

> 面向微服务故障诊断场景的可恢复 Agent RCA 平台，通过 Agent 编排、多源工具调用、确定性异常分析和可靠运行机制，实现自动化故障定位与诊断。

核心卖点：

**不是 Multi-Agent。**

而是：

> Agent 能调用工具完成复杂任务，并且任务失败后可以恢复。


---

# 最终简历应该长这样（目标）

## OpsPilot：面向微服务故障诊断的可恢复 Agent RCA 平台

技术栈：
Python、LangGraph、FastAPI、PostgreSQL、Redis、Docker Compose、LLM API

### 1. Agent RCA Workflow

你保留 DeepRCA：

```
Alert
 ↓
Coordinator Agent
 ↓
Planner
 ↓
Domain Tools / Expert Agent
 ↓
Evidence Collector
 ↓
Root Cause Agent
 ↓
Report
```

能力：

- 告警解析
- 任务拆解
- 工具调用
- 证据融合
- 根因推理


---

### 2. Deterministic + LLM RCA

这是 DeepRCA 的亮点，保留：

不用让 LLM 直接猜。

流程：

```
Metrics
 Logs
 Changes
 Trace

 ↓

异常检测
(IQR / 波动检测)

 ↓

规则筛选

 ↓

Evidence

 ↓

LLM总结
```

简历写：

> 结合异常检测算法和专家规则降低 LLM 幻觉，提高根因定位可靠性。

---

### 3. Agent Runtime（你新增的核心）

这是你和普通 Agent 项目的区别。

补：

## Task Persistence

保存：

- 当前任务状态；
- 当前执行步骤；
- Agent 输出。


PostgreSQL。


---

## Async Worker

不要：

FastAPI 请求一直等 Agent。

改：

```
API

↓

Redis Queue

↓

Worker

↓

Agent执行

↓

Database
```


---

## Checkpoint Recovery

重点。


例如：

Agent：

```
Step1 完成
Step2 DB分析完成
Step3 RPC分析中
```

挂了。


恢复：

```
读取checkpoint

↓

继续Step3
```

---

## Retry + Idempotency

简单实现：

任务：

```
request_id
```

避免重复提交。


工具：

```
tool_call_id
```

避免重复执行。

---

### 4. Evaluation

这个你最擅长，必须继承 EvalRAG 风格。

建立：

故障数据集：

例如：

50个故障场景。


指标：

- Root Cause Hit@1
- Root Cause Hit@3
- Evidence Recall
- Tool Success Rate
- E2E Success Rate
- Recovery Success Rate
- P95 Latency


---

# 在 DeepRCA 基础上，你真正需要新增什么？

按优先级：

---

## 必做（形成第二项目差异）

### ① Runtime Layer ⭐⭐⭐⭐⭐

DeepRCA：

Agent 能跑。

你升级：

Agent 能稳定运行。

新增：

- Task Manager
- PostgreSQL状态保存
- Redis Queue
- Worker


---

### ② Checkpoint + Recovery ⭐⭐⭐⭐⭐

这是项目最大亮点。

实现：

- 保存 Agent State；
- Worker 崩溃；
- 恢复。


面试非常好讲。


---

### ③ Evaluation Benchmark ⭐⭐⭐⭐⭐

把你的 EvalRAG 经验复制过去。

建立：

```
故障输入

↓

Agent分析

↓

结果评价
```


---

## 推荐做（增强）

### ④ Tool Registry

统一管理：

```
query_metric()
query_log()
query_trace()
```

不要每个 Agent 自己写。


---

### ⑤ Fault Injection

做几个模拟故障：

例如：

- Redis连接耗尽
- DB慢查询
- RPC超时
- 错误率上涨


这样项目真实感提升很多。


---

# 不建议做

## ❌ 10个Agent

不要。

两个就够：

```
Coordinator

Root Cause Agent
```

领域分析先作为 Tool。


---

## ❌ MCP

可以了解，但不是重点。

这个项目最大价值不是 MCP。


---

## ❌ K8s

不要。

Docker Compose 足够。

---

# 最终两个项目组合

你的简历会非常合理：

---

## EvalRAG

证明：

> 我会做 RAG Agent 应用。


关键词：

- Retrieval
- Hybrid Search
- Evaluation
- Trace
- Regression
- Citation


---

## OpsPilot

证明：

> 我会做 Agent Runtime 和复杂任务系统。


关键词：

- Tool Calling
- Workflow
- Async Worker
- Checkpoint
- Recovery
- Evaluation


---

刚才那两千行属于“以后你真的开工时的设计文档”，现在阶段只需要记住这三个词。你不用现在就背 Redis Streams、Lease、DLQ 这些细节。等真正实现到那个模块，再补对应知识。你现在先把项目方向固定下来即可。