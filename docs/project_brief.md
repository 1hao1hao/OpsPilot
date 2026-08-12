# OpsPilot Project Brief

## 1. 项目定义

OpsPilot 是一个面向微服务故障诊断场景的可恢复 Agent RCA 平台：接收告警后，由 Agent 规划并调用指标、日志、变更和调用链工具，结合确定性异常检测与规则生成 Evidence，再由 Root Cause Agent 输出结构化根因报告；诊断任务由独立 Worker 异步执行，并可在进程中断后从 Checkpoint 恢复。

项目的重点不是增加 Agent 数量，而是证明两件事：

1. Agent 能基于真实工具结果完成可评测的故障诊断；
2. Agent 任务失败后能够恢复，并且重复请求或重试不会产生重复执行结果。

## 2. 目标用户与核心场景

目标用户是需要快速判断故障方向的 SRE、后端工程师和平台工程师。

核心场景：

- 告警诊断：输入服务告警，返回 Top-3 根因、置信度、证据链和处置建议；
- 异步长任务：API 提交后立即返回 `run_id`，Worker 在后台完成分析，客户端查询状态或订阅进度；
- 中断恢复：Worker 在诊断中途退出后，新 Worker 从最近 Checkpoint 继续，而不是从头重复所有工具调用；
- 离线评测：在固定故障数据集上比较 RCA 方案，输出 Hit@1、Hit@3、Evidence Recall、工具成功率、端到端成功率、恢复成功率和 P95 延迟。

## 3. 为什么基于 DeepRCA

当前仓库已经提供可复用的 RCA 业务链，适合作为 OpsPilot 的业务执行内核，而不需要重写：

- FastAPI REST/WebSocket 接口；
- `intake -> planner -> dispatcher -> collector -> root_cause -> reporter` LangGraph 主图；
- Coordinator、DB/Redis/Kafka/RPC 领域分析和 Root Cause Agent；
- Metrics、Logs、Changes、Trace、Topology、Related Alerts 工具；
- IQR、波动检测、同比/环比、Noise Filter 和专家规则；
- 8 个可注入故障场景、Docker Compose 和单元/冒烟测试。

当前实现仍是“API 进程内运行 Agent”：`POST /analyze` 创建后台协程，状态写入带 1 小时 TTL 的 Redis，Redis 不可用时退回进程内字典。API 进程或 Worker 退出后没有持久 Run/Step、Checkpoint 或恢复链路。现有 8 个场景也主要通过根因文本关键词重叠验证，没有冻结数据集、逐 case prediction 和正式 Benchmark 报告。

## 4. 能力边界

| 分类 | 已有 DeepRCA 能力 | OpsPilot 新增贡献 |
|---|---|---|
| RCA 工作流 | 告警解析、六维分析、领域 Expert、证据汇聚、根因报告 | 收敛为 Coordinator + Root Cause 两个 Agent；领域能力经统一 Tool Registry 调用 |
| 确定性分析 | IQR、波动检测、同比/环比、噪声过滤、8 条规则 | 统一 Evidence 契约、可版本化规则与可评测输出 |
| 执行方式 | FastAPI 进程内 `asyncio.create_task` | PostgreSQL Task/Step 状态、Redis Queue、独立 Worker |
| 可靠性 | 单次工具/Expert 超时降级 | Checkpoint 恢复、Retry、请求与工具调用幂等 |
| 状态存储 | Redis TTL；内存降级 | PostgreSQL 作为持久事实源，Redis 只负责队列 |
| 验证 | 8 个可注入场景、关键词匹配、单元/冒烟测试 | 版本化 RCA 数据集、固定 split、多指标评测、失败回归 |
| 运行证据 | 最终报告和粗粒度状态 | Run/Step/Tool 事件、Checkpoint、逐 case prediction、评测报告 |

由于当前工作目录不是 Git 仓库，以上边界依据现有代码、README、PRD、`plan_new.md` 和 `plan.md` 判断，不能用提交历史进一步核验作者贡献。

## 5. MVP 输入与输出

### 在线输入

`POST /api/v1/runs` 接收：

```json
{
  "request_id": "alert:prod:order:20260811-001",
  "alert": {
    "alert_id": "alt-001",
    "service_name": "order-service",
    "alert_type": "timeout",
    "severity": "P1",
    "timestamp": "2026-08-11T10:00:00Z",
    "description": "P99 latency increased",
    "labels": {"env": "production", "cluster": "prod-01"}
  }
}
```

API 必须快速返回 `run_id`、状态和查询地址；重复 `request_id` 返回同一个 Run。

### 在线输出

最终报告至少包含：

- `run_id`、状态、开始/结束时间和是否经过恢复；
- `root_cause_type`、根因描述、Top-3 候选和置信度；
- 支持证据与反证；
- 实际工具调用及失败信息；
- 可执行但只读安全边界内的处置建议。

### 离线输入输出

离线输入为版本化 `EvaluationCase + RunConfig`。输出为系统实际生成的逐 case predictions、聚合 metrics、失败分类和可复现 manifest。禁止手填 prediction 或只保留平均分。

## 6. 首个完整版本范围

必须完成：

- 保留并迁移现有 RCA 主链，HTTP 兼容接口在迁移期可继续工作；
- Coordinator 与 Root Cause 两个 Agent，领域诊断能力作为 Tool；
- Tool Registry 与统一执行入口；
- PostgreSQL 持久化 Run、Step、Checkpoint、ToolExecution 和 Report；
- Redis Queue 与独立 Worker；
- Step 边界 Checkpoint、失败重试、`request_id` 与 `tool_call_id` 幂等；
- 版本化业务故障场景、固定 dev/frozen-test split 和评测 CLI；
- Worker 中断、工具超时、重复请求三个最小可靠性场景；
- Docker Compose 一键运行 API、Worker、PostgreSQL、Redis 和 Mock Environment。

明确不做：

- 自动重启、回滚或修改生产资源，只做诊断和建议；
- K8s 部署、MCP、复杂权限平台、前端和 Grafana；
- 十个 Agent 或动态自治 Agent 群；
- 第一版不实现 Redis Streams Consumer Group、Lease/Heartbeat、DLQ、优先级调度等扩展机制；只有实测证明简单队列无法满足恢复目标时再立项；
- 不保存或展示模型私有 Chain-of-Thought，只保存简短 decision rationale。

## 7. 成功标准

以下均为目标值，未运行前不得写成实测结果：

- 数据集包含 30 至 50 个有 ground truth 的故障/正常/噪声 case，并固定版本和 split；
- 输出 Root Cause Hit@1、Hit@3、Evidence Recall、Tool Success Rate、E2E Success Rate、Recovery Success Rate 和 P95 Latency；
- Hybrid RCA 与至少一个可复现 baseline 使用同一数据、配置和指标口径比较；
- Worker 中断后 Run 能从最近成功 Step 恢复并最终完成；
- 同一 `request_id` 不创建两个 Run，同一 `tool_call_id` 不生成两份成功记录；
- 每个修复过的真实失败都有 executable regression case；
- README 最终可演示正常 RCA、异步执行、Worker 恢复和 Benchmark 四条链路；
- 简历中的所有数字都能追溯到 `artifacts/evaluations/<evaluation_id>/` 下的真实工件。

## 8. 当前基线与约束

2026-08-11 的只读检查结果：

- 当前仓库包含约 11.8k 行 Python 源码与测试，已有单元测试、冒烟测试，集成测试目录为空；
- 直接执行 `pytest -q tests/unit` 在收集阶段失败，原因是当前环境没有安装 `src` 包且缺少 `pydantic_settings`/pytest-asyncio；这不是业务断言失败；
- 第一阶段必须先建立可复现安装命令并记录现有测试真实结果，之后才能以其作为重构回归基线；
- 当前 `AlertEvent`、`DeepRCAState`、`SubAgentResult` 与领域维度命名存在多套口径，迁移前必须先冻结统一 schema；
- 不通过修改 frozen test、删除失败 case 或人工编辑 predictions 来获得更好的指标。

