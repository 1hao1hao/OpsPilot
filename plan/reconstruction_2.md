继续重构：
这是第二阶段。

前提：
第一阶段已经完成 RCA 主流程收敛，当前核心链路应该已经接近：

Alert
→ Coordinator
→ L1 Tools
→ ToolResult
→ Evidence
→ RootCauseRanker
→ Top-K
→ Evidence Gate
→ Planner（证据不足）
→ Tool / Expert
→ 新 Evidence
→ Rank + Gate

本阶段目标：

“清理外围历史负担，让项目代码、API、Report、README 都围绕这一条主链，同时保留异步 Runtime 作为独立工程卖点。”

在开始修改前先重新检查当前代码和第一阶段实际变更，不要假定第一阶段一定严格按照原计划完成。

==================================================
一、Runtime 与 RCA Engine 明确解耦
==================================================

项目认知模型应该清楚分成两个部分：

1. RCA Engine：负责“怎么诊断”

Alert
→ Coordinator
→ Tools / Experts
→ Evidence
→ RootCause Ranking
→ Evidence Gate
→ Adaptive Planner

2. Runtime：负责“怎么可靠执行诊断”

API
→ PostgreSQL Run
→ Redis Queue
→ Worker
→ Checkpoint
→ Recovery

Runtime 不应该理解：

- 六维分析
- IQR 的内部细节
- RuleEngine
- AlgorithmSignal
- Expert Finding

Runtime 只需要保存/恢复 RCA Engine 运行所需的业务状态。

保留：

- Run 状态机
- Redis Queue
- PostgreSQL truth source
- Worker
- Checkpoint
- stale run recovery
- ToolCall idempotency
- retry / timeout

不要重写这些已经工作的能力。

==================================================
二、简化 DeterministicPlannerFallback
==================================================

当前 deterministic fallback 不应该继续维护一套几乎可以独立完成 RCA 的复杂规则 Planner。

目标：

LLM Planner 成功：
→ 使用 LLM 决策。

LLM Planner 失败：
→ 一个非常简单、确定、可预测的 fallback。

Fallback 只负责：
“从合法且尚未执行的动作里选择一个合理的下一步”。

禁止重新实现另一套完整语义推理系统。

可以采用类似优先级：

1. 如果存在 Evidence 明确指向某个尚未调用的领域，并且还有 Expert budget：
   → invoke 对应 Expert。

2. 否则从尚未执行的 general tools 中按一个固定、简单的优先级选择一个。

3. 没有合法动作：
   → None。

允许保留少量非常直观的映射，例如：
db evidence → DB Expert
redis evidence → Redis Expert
kafka evidence → Kafka Expert
rpc evidence → RPC Expert

但不要继续维护大量：
- DOMAIN_HINTS 文本关键词
- alert_type defaults
- supplementary tool 规则
- 复杂 context 拼接
- 第二套语义 Planner

Fallback 的价值是可靠兜底，不是替代 LLM Planner。

==================================================
三、删除 deprecated /api/v1/analyze 兼容接口
==================================================

当前主 API 已经是：

POST /api/v1/runs
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/result
GET /api/v1/runs/{run_id}/events
WS  /api/v1/runs/{run_id}/stream

删除旧兼容接口：

POST /api/v1/analyze
GET /api/v1/analyze/{run_id}/status
GET /api/v1/analyze/{run_id}/result

以及只为了旧接口存在的：
- compatibility adapter
- compat.py 中无其他必要用途的代码
- 相关 tests
- README 文档

项目不需要同时维护两套 API。

如果发现 compat.py 还有其他正在使用的功能，先检查依赖，只删除真正的 legacy adapter。

==================================================
四、检查并删除平行 Workflow
==================================================

重点检查：

src/opspilot/graph/workflow.py

当前项目已经有：

API
→ TaskManager
→ Queue
→ RuntimeWorker
→ RecoverableExecution
→ AdaptiveInvestigator

如果 OpsPilotWorkflow 只是一个旧的 in-process 平行执行路径，导致：

“同一个 RCA Engine 有两套入口和两套 report 构造逻辑”

则进行清理。

优先方案：

- 删除不再需要的平行业务执行路径；
或者
- 把它降级成非常薄的 wrapper，真正业务逻辑只能调用同一个 RCA Engine。

禁止维护：

Runtime 一套 RCA 拼装逻辑
+
OpsPilotWorkflow 另一套 RCA 拼装逻辑。

recommended_actions 如果仍有价值，可以移动到更合理的 diagnosis/report 模块，不要因为删除 workflow 丢掉。

先通过全局依赖和 tests 确认用途再删除。

==================================================
五、简化 DiagnosisReport
==================================================

当前最终 Report 不应继续暴露大量内部中间状态。

面向业务/API 的最终结果重点应该是：

- trace_id / run_id
- alert_id
- service_name
- Top-K candidates
- primary_root_cause
- Evidence
- decision_rationale
- recommended_actions
- 调查结果摘要
- latency / degraded 等必要运行状态

不要继续把以下旧内部结构作为最终业务 Report 的核心字段：

- dimension_results
- expert_results
- algorithm_signals
- matched_rules

如果第一阶段仍为了兼容临时保留这些字段，本阶段正式移除。

Tool 调用详情、Action History、Gate Decision 等：

不要全部平铺进 DiagnosisReport。

详细内部信息应该通过：
- InvestigationTrace
或
- /events
查询。

最终 Report 保持简洁。

==================================================
六、简化 InvestigationTrace
==================================================

Trace 可以保留：

- rounds
- action_history
- gate_decisions
- executed_tools
- invoked_experts
- stop_reason
- tool_budget_used
- expert_budget_used

如果 duplicate_actions 等字段确实用于调试可以保留，但不要继续扩充。

Trace 的定位：

“解释 Agent 为什么这样调查”。

不是另一个业务状态数据库。

==================================================
七、进一步清理旧模型和死代码
==================================================

完成第一阶段和 Report/API 改造后，进行一次全局依赖扫描。

重点检查旧概念是否已经完全没有业务价值：

- DimensionTask
- SemanticAnalysisResult
- AlgorithmSignal
- ExpertRuleEngine
- matched_rules
- collect_semantic_evidence
- collect_expert_evidence
- analyze_dimensions
- analyze_experts
- _legacy_results
- 老的 Rule / diagnostics helper
- legacy API schemas
- 无引用的 constants / enums

如果没有实际引用：
直接删除。

如果只被旧测试引用：
删除旧测试。

不要为了“也许以后有用”留下大量死代码。

==================================================
八、代码目录目标
==================================================

不要为了目录漂亮强制搬所有文件。

但业务结构最终应该让人容易理解成：

diagnosis / investigation
    coordinator
    planner
    evidence / anomaly
    ranker
    engine

tools
    registry
    executor

runtime
    task_manager
    queue
    worker
    checkpoint / repository interaction

具体目录可以根据当前代码最小改动决定。

原则：

从目录和 import 就能看出：

RCA Engine
Tools
Runtime

三层职责。

不要为了重构目录引入大量无意义 rename。

==================================================
九、README 更新
==================================================

代码完成后更新 README。

README 不要再重点描述大量内部 Rule、Dimension、错误类型。

核心架构只讲：

Alert
↓
Coordinator
↓
L1：Metrics / Logs / Traces / Changes
↓
Evidence
↓
Historical Baseline + IQR + Volatility Detection
↓
Deterministic RootCause Ranking
↓
Evidence Gate
↓
证据不足：
LLM Planner → Tool / Expert → 新 Evidence
↓
最终 Top-K RCA

然后独立介绍 Runtime：

API
→ PostgreSQL Run
→ Redis Queue
→ Worker
→ Checkpoint
→ Recovery

强调设计边界：

LLM 负责下一步调查方向；
确定性代码负责 Tool 权限、Evidence、异常检测、Gate、预算和最终根因排名。

==================================================
十、自我介绍对应的架构必须成立
==================================================

重构后的真实代码必须能够支撑下面这段项目介绍：

“这是一个面向微服务故障诊断的自适应 Multi-Agent RCA 系统。整体采用 Coordinator → L1 广度分析 → L2 领域深挖 → L3 确定性根因分析的分层架构：Coordinator 负责编排调查流程，L1 先进行多维度广度排查，发现明确领域线索后进入对应 Expert 深挖，最后由 L3 结合确定性异常检测和 Evidence 聚合得到 Top-K 根因。其中异常分析主要结合历史基线对比、IQR 和滚动波动检测。证据不足时，系统会根据当前 Evidence 动态决定下一步调查方向，同时还实现了可恢复的异步任务执行链路。”

注意：
这里的“L1 多维度”现在主要指：

Metrics
Logs
Traces
Changes

不要再重新设计六维语义状态。

==================================================
十一、测试要求
==================================================

运行完整 tests。

重点验证：

1. 新 /api/v1/runs 链路正常。
2. 删除 legacy analyze API 后没有残留依赖。
3. Worker 正常执行 RCA。
4. Checkpoint 正常保存和恢复。
5. ToolCall 幂等仍正常。
6. stale run recovery 正常。
7. LLM Planner 正常。
8. LLM Planner 失败后的简化 fallback 正常。
9. Evidence Gate 正常。
10. 最终 DiagnosisReport 不再包含旧复杂中间结构。
11. WebSocket / events 正常。
12. 现有关键 RCA Case 的 Top-K 结果没有因为清理外围代码出现明显退化。

==================================================
十二、避免这些错误
==================================================

禁止：

- 再增加一套新的 Agent State 抽象
- 引入 LangGraph/LangChain
- 新增复杂 Rule Engine
- 新增更多错误类型
- 把 Runtime 和 RCA 重新耦合
- 为已经删除的 legacy 设计增加 compatibility shim
- 为旧 checkpoint 保留大量历史转换代码
- 顺手大改 EvalRAG
- 顺手增加新的业务功能

这是“收敛”任务，不是功能扩展任务。

==================================================
十三、最终验收标准
==================================================

一个第一次看代码的人，应当能够很快看出：

业务主链：

Coordinator
→ ToolResult
→ Evidence
→ RootCause Ranking
→ Evidence Gate
→ Planner
→ Tool / Expert
→ Evidence

可靠执行链：

API
→ Queue
→ Worker
→ Checkpoint
→ Recovery

不应该需要先理解：

六维结果
→ Semantic Finding
→ AlgorithmSignal
→ RuleMatch
→ Derived Evidence
→ 第二套 Planner
→ 旧 API
→ 平行 Workflow

才能看懂项目。

==================================================
十四、完成后给出报告
==================================================

请最后输出：

1. 删除的文件/类/模型。
2. 合并的概念。
3. 当前真实业务主链。
4. 当前 Runtime 链。
5. API 变化。
6. DiagnosisReport 变化。
7. Fallback Planner 如何简化。
8. README 如何更新。
9. 完整测试结果。
10. 代码中还存在但暂时没必要继续清理的技术债。

如果遇到旧设计与新设计冲突：
优先选择“更简单、能支持项目卖点、容易解释”的方案。

不要为了保持旧测试通过而重新引入被删除的复杂度。
应该更新测试，使测试验证新的设计目标。