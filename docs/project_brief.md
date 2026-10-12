# OpsPilot 项目定位

更新：2026-10-12。OpsPilot 是面向 SRE、后端与平台工程师的可恢复微服务 RCA 系统。输入告警，调查 Metrics、Logs、Traces 和领域数据，输出结构化证据、候选和确定性三终态；API 与诊断执行分离，Worker 可从 Checkpoint 恢复。

## 已实现能力

- Coordinator 的低成本 Seed、可选 LLM 调查规划、确定性 Fallback、领域 Expert 与只读 ToolRegistry。
- ToolResult → Evidence/异常检测 → 确定性 Top-K；动态证据需求、独立 Fault Presence、三终态和未确认候选。
- PostgreSQL 事实源、Redis run_id 队列、独立 Worker、状态查询/事件/WebSocket、请求与工具幂等、stale Run 恢复。
- Mock 与 Prometheus/Jaeger/OpenSearch 适配、版本化数据集、真实 Astronomy Shop harness、隔离的 Ground Truth 与实验工件。

## 输出边界

`CONFIRMED` 表示观测和 Gate/机制校验充分；`NO_FAULT` 只覆盖当前告警与窗口；`INCONCLUSIVE` 表示无法可靠确认，允许保留 Top-K。Run 执行成功不等于诊断正确，Top1 不等于确认根因，缺数据不等于正常。处置建议不自动操作环境。

## 当前验证水平

最近完整真实发布的 Full Top1 为 16/18、Normal 3/3；后续冻结实验中断，部分 14/17 不能作为完整验收。新三终态已实现、快速测试和既有工件离线回放通过，但未重跑新的真实故障生命周期，确认覆盖率仍有限。项目没有企业生产数据，不声明生产泛化、18/18 命中或 100% 确认准确率。

未接入知识检索/EvalRAG，未实现自动修复、Kubernetes 生产交付或复杂自治 Agent 集群。旧 DeepRCA 诊断主链已移除，保留 Mock 服务；具体事实以当前代码、[README](../README.md) 与 [验收报告](../reports/task_10.9.md) 为准。[原立项基线](history/20261012/project_brief.md) 保留供追溯。
