# OpsPilot V2 面试说明

## 三分钟介绍

OpsPilot 将微服务告警转化为可恢复的只读 RCA 调查。原系统用固定的 Coordinator、确定性证据分析和单一调查循环；V2 增加 LangGraph 动态 Task Graph，将观测中的依赖与未满足需求分给受限的 DB、Redis、Kafka、RPC Specialist，并行收集后重新分析。Skill 提供按需加载的领域步骤，MCP SDK 提供标准工具发现与调用。所有 Agent 只提出调查动作；Evidence Engine、Ranker、Gate 和三终态决策仍由确定性代码执行。PostgreSQL 保存 Run 与 Checkpoint，Redis Streams 分发任务，Worker 心跳与轮次防止旧 Worker 覆盖状态。V2 当前显式启用，V1 保持默认用于旧 Run 和可比基线。

## 高频问题

1. **为什么用 LangGraph？** 需要显式条件分支、`Send` 并行派发及汇合后重规划；图实际执行节点，而非画图标签。
2. **Task 如何产生？** 告警需求、Top-K 区分需求和 trace 中实际观察到的依赖构成版本化 Task。
3. **什么证明计划是动态的？** 新 ToolResult 改变需求覆盖、任务 READY/SATISFIED 和优先级；演示事件记录每轮变化。
4. **Specialist 是否只是工具包装？** 它持有局部目标、证据、预算和白名单，可逐步选择工具并返回结构化局部结果；最终结论仍归确定性决策。
5. **并行如何保证一致性？** 每个 Specialist 使用独立上下文，合并按 Task ID 排序，PG 写入受 Worker 轮次约束。
6. **为什么保留原 Evidence Engine？** 它把 ToolResult 统一转为可追溯 Evidence，使排名与决策不依赖模型自由文本。
7. **ToolResult 为什么还进质量判断？** 失败、空数据、缺基线和范围外观测可能没有 Evidence，但会影响能否确认或排除故障。
8. **Gate 与 Verdict 有何差别？** Gate 初筛 Top1 评分、分差和来源；Verdict 还检查存在性、有效支持、区分需求与矛盾。
9. **为什么有 INCONCLUSIVE？** 缺失或冲突观测不能安全变成 `NO_FAULT` 或 `CONFIRMED`；预算耗尽也保留未确认结果。
10. **Skill 有什么实际作用？** Catalog 先选元数据、激活时加载正文，并影响授权工具顺序；加载记录可审计。
11. **MCP 是真调用吗？** 使用官方 SDK 的 Server/Client 发现和调用；当前为进程内实现，远程认证未交付。
12. **LLM 能否绕过工具权限？** 不能。结构化输出经 schema、白名单和预算验证，失败回退确定性选择。
13. **怎样恢复崩溃？** Worker 重新领取 stale Run，加载 PG Checkpoint，复用已持久化成功的 ToolCall，再分析剩余状态。
14. **这是否 Exactly-Once？** 不是。状态写入有轮次栅栏，已持久化 ToolCall 可复用；崩溃窗口内计算仍可能重复。
15. **为什么 V2 不设为默认？** 旧 Run 的 checkpoint schema 与行为契约不同；显式切换提供可控迁移和回滚。
16. **评测如何避免泄漏答案？** 数据集 Ground Truth 仅由评分器读取，在线调查仅看 Alert 和工具观测。
17. **消融的局限？** 旧 Mock snapshot 缺少部分依赖观测；无模型的离线结果不能代表真实故障注入或生产准确率。
18. **V2 是否一定更优？** 不预设。应同时比较 Top1、确认覆盖、误确认、拒判、调用量和 p95，保留负结果。

面试展示可运行 `scripts/v2_demo.py`，再打开 `artifacts/v2_demo/demo.json` 的 `events`、`task_graph` 和 `evidence_board`。该场景是合成测试；真实遥测验收范围与结果见 [V2 评测](V2_EVALUATION.md)。
