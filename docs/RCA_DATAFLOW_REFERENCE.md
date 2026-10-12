# 当前 RCA 数据流

更新：2026-10-12。[旧 Runtime v4 审计](history/20261012/RCA_DATAFLOW_REFERENCE.md) 描述旧实现，不作为当前符号或工具清单。

| 顺序 | 当前实现 | 数据与控制 |
| --- | --- | --- |
| 首轮 | `CoordinatorAgent.plan` | 从公开告警信息选择 2–3 个 General Tool |
| 工具执行 | `ToolExecutor` / Runtime 注入执行函数 | 验证 schema、超时/重试；统一 ToolResult 与执行事件 |
| 每轮分析 | `DeterministicEvidenceEngine.analyze` | 累计 ToolResult → collect_evidence + AnomalyDetector → 去重 Evidence → 排名 |
| 根因可信度 | `EvidenceGate.evaluate` | 非 no_fault、confidence、margin、独立来源数 |
| 决策 | `decide_diagnosis` | 观测范围/质量、需求覆盖、Fault Presence、机制校验、矛盾和 Verdict |
| 后续规划 | `LLMAdaptivePlanner.decide` | 未满足需求、允许动作与剩余预算；非法/失败输出回退 |
| 停止 | `AdaptiveInvestigator.run` | CONFIRMED / NO_FAULT；预算耗尽或无合法动作保留 INCONCLUSIVE |
| 最终报告 | `build_report` | 使用当前 Evidence/候选；重新确定决策，不重跑排名或让 LLM 改结论 |

`ToolResult.data.observations` 是统一观测入口。Evidence 保留 source_group、supports、contradicts 和 raw_ref；Gate 按来源计数，Ranking 按支持/反驳证据计分。不存在单独的 L1 SemanticAnalysisResult、L2 Finding promotion 或 finalize Action。

存在性不能从 Top1 推导：PRESENT 来自有效异常；ABSENT 需要健康参考和充分覆盖；UNKNOWN 表示无法判断。候选反证可以使根因不确定，不能抹去独立观测的故障。错误服务、旧 spans、孤立 DB 尾延迟和缺失协议结果不能冒充当前机制证据。

缺失需求每轮更新并反馈 Planner；异步完成度独立于通用延迟。正常领域测量可帮助区分候选，但成功空结果不能 discharge 健康需求。证据质量细节以 `investigation/decision.py` 为准。

Trace 与 Checkpoint 保存决策和历史；恢复后从观测重新判断。最终报告 schema 3.0，旧报告保持兼容且缺新字段时未确认。API 执行信息和工具 attempt 在事件中；最终 Verdict 与 Run 状态分离。核心架构及兼容边界见 [架构说明](architecture.md)。
