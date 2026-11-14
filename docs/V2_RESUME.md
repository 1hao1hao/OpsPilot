# 简历项目描述：OpsPilot

**微服务可恢复根因调查系统（Python / LangGraph / PostgreSQL / Redis Streams / MCP）**

- 在既有 RCA 系统上实现可选 V2 LangGraph 调查路径：按观测需求生成版本化任务，使用 `Send` 并行调度领域 Specialist，汇合后重新计算证据、候选和任务计划。
- 实现受限 Specialist 局部调查、结构化 LLM 工具选择及确定性回退；按需加载 Skill，并以官方 MCP SDK 完成进程内工具发现与只读调用。
- 扩展 Evidence Board 的任务、Agent、ToolCall、服务和时间来源；复用确定性 Evidence Gate 与 `CONFIRMED / NO_FAULT / INCONCLUSIVE` 决策，不让模型直接确认根因。
- 复用 PostgreSQL Checkpoint、ToolCall 幂等、Redis Streams 与 Worker 租约/轮次，验证崩溃后恢复；明确不保证计算过程 Exactly-Once。
- 构建 V1/V2 六组可复现消融及合成多依赖演示，记录 Top1、确认覆盖、拒判、调用量、延迟与负结果；真实 OpenTelemetry 探针仅验证只读数据链路，不声称生产准确率。

使用本描述前请核对 [V2 评测](V2_EVALUATION.md) 中的最终数字与测试记录；不要把合成演示写成真实生产故障处理。
