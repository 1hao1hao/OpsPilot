你现在需要重构项目：
目标不是增加功能，而是“做减法”，降低项目认知复杂度，让代码结构与面试时讲述的主线完全一致。

在开始修改前：
1. 先完整检查当前代码，不要只看 README。
2. 重点阅读：
   - src/opspilot/investigation/
   - src/opspilot/agents/
   - src/opspilot/evidence/
   - src/opspilot/rca/
   - src/opspilot/tools/
   - src/opspilot/runtime/execution.py
   - src/opspilot/models/
   - 相关 tests
3. 根据实际依赖关系修改，不要机械按照文件名操作。
4. 本任务禁止增加新的 Agent、规则层、抽象层、中间状态或框架。
5. 不要修改 EvalRAG 仓库。
6. 不要为了兼容旧设计继续保留一套平行数据流。这个项目是个人项目，不需要背负复杂历史兼容成本。

==================================================
一、最终希望得到的唯一 RCA 主链
==================================================

目标数据流：

Alert
↓
Coordinator
↓
L1 低成本广度调查
Metrics / Logs / Traces / Changes
↓
ToolResult
↓
Evidence Builder / Anomaly Detector
↓
Evidence Pool
↓
RootCauseRanker
↓
Top-K Root Cause
↓
Evidence Gate

如果 Gate PASS：
→ Final RCA

如果 Gate FAIL：
→ LLM Planner
→ 选择 inspect_tool 或 invoke_expert
→ Tool / DB、Redis、Kafka、RPC Expert
→ 新 ToolResult
→ 新 Evidence
→ 重新 Rank + Gate

直到：
- Evidence Gate 通过
或
- 调查预算耗尽

核心原则：

ToolResult → Evidence → RootCauseCandidate

这是整个业务数据流的核心。

控制数据只保留：
- InvestigationAction
- EvidenceGateDecision
- 调查预算 / executed_tools / invoked_experts

==================================================
二、必须保留的项目卖点
==================================================

以下能力不能因为重构被删掉：

1. Coordinator 首轮低成本广度调查。
2. Metrics / Logs / Traces / Changes 四类 L1 调查能力。
3. DB / Redis / Kafka / RPC 领域 Expert。
4. LLM Adaptive Planner。
5. Planner 只允许两种动作：
   - inspect_tool
   - invoke_expert
6. ActionValidator，代码层约束 LLM 可以调用哪些 Tool / Expert。
7. Evidence Gate。
8. 调查轮数 / Tool / Expert Budget，防止 Agent 无限循环。
9. 确定性 RootCause Ranking。
10. 历史基线比较。
11. IQR 异常检测。
12. Rolling / Volatility 波动检测。
13. Tool Registry / Tool Executor。
14. LLM 不直接决定最终根因，只决定“下一步查什么”。
15. 最终根因仍由确定性 Evidence + Ranker 得到。

不要改变这个设计边界：

LLM：
负责开放式的下一步调查决策。

确定性代码：
负责权限、异常检测、Evidence、Gate、预算和最终 RootCause Ranking。

==================================================
三、删除“六维分析作为核心中间层”
==================================================

当前代码存在：

change
upstream
downstream
cluster
errorlog
problem

以及：
DimensionTask
dimension_results
SemanticAnalysisResult
Tool → Dimension 映射
L1 Finding → Evidence

这些让数据流非常绕。

新的主流程中不要再依赖：

ToolResult
→ Dimension
→ Semantic Finding
→ Evidence

而应该尽可能直接：

ToolResult
→ Evidence Builder
→ Evidence

L1 对外概念只保留：

Metrics
Logs
Traces
Changes

如果某些原有分析逻辑仍然有价值，可以直接融合进 Evidence Builder / Analyzer 内部，但不要再让“六维 Dimension”成为整个系统必须携带的业务状态。

要求：

- AdaptiveInvestigator 的核心 state 不再依赖 dimension_results。
- Planner 不再需要 dimension_results。
- Runtime checkpoint 不应为了 RCA 主流程保存大量 dimension_results。
- InvestigationOutcome 不应把 dimension_results 作为核心结果。
- 最终报告也不要依赖 dimension_results。

如果 SemanticAnalysisResult、DimensionTask 等类型在完成重构后没有必要继续存在，则删除。
如果仍有极少量内部用途，也不要让它们进入主链。

==================================================
四、L2 Expert 同样统一到 Evidence
==================================================

当前存在类似：

ToolResult
→ Expert Finding
→ Expert Evidence

希望统一成：

Planner invoke_expert
↓
Expert 决定需要哪些 Domain Tools
↓
执行 Domain Tools
↓
ToolResult
↓
Evidence Builder
↓
Evidence Pool

保留：
- db.replication
- db.slowlog
- db.connections
- redis.memory
- redis.hotkeys
- kafka.lag
- rpc.metrics

以及合理的 Expert Tool 选择逻辑。

但不要继续维护 expert_results 作为一个和 Evidence 平行的核心数据结构。

Planner 后续判断应该主要依赖：
- Alert
- 当前 Evidence
- provisional Top-K
- executed_tools
- invoked_experts
- action_history
- remaining budget

而不是再依赖 L1/L2 Finding 对象。

==================================================
五、合并 L3 异常分析
==================================================

当前 L3 有较多概念：

MetricFilter
NoiseFilter
MultiDimensionComparator
QuantileAnomalyDetector
VolatilityDetector
ExpertRuleEngine
AlgorithmSignal
matched_rules
Rule → Evidence

现在需要收敛成一个清晰概念：

AnomalyDetector
↓
Evidence

必须保留并真正执行的核心检测方法：

1. Historical Baseline
   当前指标与历史/同期基线对比。

2. IQR
   判断当前值是否偏离历史分布。

3. Rolling / Volatility Detection
   判断近期波动程度是否发生明显变化。

这些内部可以继续拆成几个实现类，但“对业务主流程的输出”必须统一为 Evidence。

例如：

Metric ToolResult
↓
AnomalyDetector
↓
Evidence(
    fact=...,
    confidence=...,
    supports=[...],
    source_group=...
)

不要再要求主流程理解 AlgorithmSignal。

如果 AlgorithmSignal 只用于 debug，可以放入内部 trace/debug，不要进入核心 state / report。

==================================================
六、删除 ExpertRuleEngine 的第二套规则链
==================================================

当前类似：

Evidence / Finding
↓
ExpertRuleEngine
↓
R001 / R002 / ...
↓
matched_rules
↓
再次生成 Evidence

这会形成：

Evidence → Rule → Evidence

属于重复的数据转换和认知负担。

要求：

- 从主 RCA 链路移除 ExpertRuleEngine。
- 删除 matched_rules 这个核心状态。
- 删除 _legacy_results 等为了旧 RuleEngine 做的数据格式转换。
- R001～R007 如果只是对已有 Evidence 再判断一次，直接删除。
- 如果个别规则包含确实必要且不存在于其他地方的业务判断，把这个判断直接融合进对应 Evidence Builder / AnomalyDetector，不要保留一个独立 RuleEngine。

最终不要再存在一条和 Evidence Builder 平行的“规则推理系统”。

==================================================
七、统一 Evidence Pool
==================================================

现在存在多种类似概念：

base_evidence
semantic evidence
expert evidence
deterministic evidence
algorithm evidence
rule evidence

主流程全部统一成：

Evidence Pool

每个 Evidence 至少能够表达：

- evidence_id
- fact
- source / source_group
- confidence
- supports
- contradicts（如果当前设计确实使用）
- 需要的 provenance 信息

Evidence 必须继续支持 Evidence Gate 判断“独立证据来源”。

所有组件最终只往同一个 Evidence Pool 增加 Evidence。

必须做好去重，不能因为同一个事实经过不同旧路径而生成重复 Evidence。

==================================================
八、RootCause Ranking 尽量保持简单
==================================================

继续保留当前确定性排名思想：

支持某个 RootCause
→ 加对应 Evidence confidence

反驳某 RootCause
→ 减对应 confidence

然后：
→ 排序
→ 输出 Top-K

LLM 不参与 RootCause 排名。

如果当前 RootCauseAgent 的 deterministic diagnose() 已能满足目标，优先复用，不要重新设计另一套评分框架。

==================================================
九、Evidence Gate 保留
==================================================

Gate 目标仍然只有：

“当前 Evidence 是否足以支持一个可信根因？”

保留目前合理的判断：
- Top1 不能是 NO_FAULT
- Top1 confidence
- Top1 / Top2 margin
- 独立 Evidence source 数量

不要增加新的复杂规则。

Gate FAIL：
→ Planner 再调查。

Gate PASS：
→ 停止。

Budget exhausted：
→ 停止并使用当前确定性结果，同时清楚记录 stop_reason。

==================================================
十、Planner 简化输入
==================================================

LLM Planner 的输入改成围绕：

- alert
- evidence
- provisional_top_k
- executed_tools
- invoked_experts
- action_history
- remaining_budget
- allowed_actions

删除：
- l1_findings
- l2_findings
- dimension_results
- expert_results

Planner 仍然只能返回：

{
  "action": "inspect_tool" | "invoke_expert",
  "target": "...",
  "reason": "..."
}

保留 ActionValidator。

Expert Tool Selection 也尽量基于：

Alert + Evidence

不要再依赖六维分析结果。

==================================================
十一、Runtime 这次不要重构
==================================================

以下设计全部保留：

API
→ PostgreSQL Run
→ Redis Queue
→ Worker
→ RecoverableExecution
→ Checkpoint
→ Recovery

以及：

- Run 状态机
- PostgreSQL truth source
- ToolCall persistent idempotency
- Checkpoint
- stale run recovery
- retry / timeout
- Redis Queue

本任务禁止重新设计这些东西。

只允许为了适配新的 InvestigationOutcome / State 做“最小必要修改”。

例如 Runtime 当前如果保存：

dimension_results
expert_results
algorithm_signals
matched_rules

可以停止保存这些核心中间状态。

新的 Checkpoint 业务状态尽量只保留：

- Alert
- ToolResults
- Evidence
- Candidates
- Investigation control state
- rationale / report 等真正恢复所需字段

不要为了兼容旧 checkpoint 再增加一套复杂转换。
这是个人项目，可以更新 checkpoint schema/version 或让 restore 简单容错。

==================================================
十二、第一阶段暂时不要做的事情
==================================================

本任务不要：

- 重写 Redis Queue
- 重写 PostgreSQL Repository
- 重写 Worker
- 重写 Checkpoint 系统
- 删除 /api/v1/analyze legacy API（放到下一个任务）
- 大改 DiagnosisReport 外部 API（放到下一个任务）
- 增加新的数据库表
- 增加新的 Agent
- 增加 MCP
- 增加 LangGraph / LangChain 等框架
- 增加新的复杂规则
- 做和本次 RCA 数据流收敛无关的重构

==================================================
十三、测试要求
==================================================

完成后：

1. 运行现有完整测试。
2. 修复因为重构导致的测试。
3. 删除只验证旧六维 / RuleEngine 实现细节、已经失去意义的测试。
4. 新增或调整测试，至少验证：

Case A：
L1 Metrics / Logs / Traces / Changes ToolResult
能够直接转成正确 Evidence。

Case B：
DB / Redis / Kafka / RPC Domain ToolResult
能够进入同一 Evidence Pool。

Case C：
历史基线 / IQR / Volatility 能产生对应异常 Evidence。

Case D：
Evidence → deterministic RootCause Ranking → Top-K 正常。

Case E：
Evidence Gate PASS 时不再调用 Planner。

Case F：
Evidence Gate FAIL 时 Planner 选择 Tool / Expert，
新 Evidence 加入后重新 Rank + Gate。

Case G：
Planner 仍不能调用未授权 Tool / Expert。

Case H：
Budget 能终止调查。

Case I：
Checkpoint 恢复后调查仍可继续，不会因为删除旧中间状态而失败。

==================================================
十四、验收标准
==================================================

最终从代码结构上能够直接看出：

Alert
→ Coordinator
→ ToolResult
→ Evidence
→ RootCauseCandidate
→ EvidenceGate
→ Planner
→ 新 ToolResult
→ 新 Evidence

而不是：

ToolResult
→ Dimension
→ Finding
→ SemanticEvidence
→ AlgorithmSignal
→ RuleMatch
→ DerivedEvidence
→ Candidate

最终核心业务状态应明显减少。

希望 AdaptiveInvestigator 的核心状态主要围绕：

- tool_results
- evidence
- provisional_candidates
- action_history
- gate_decisions
- executed_tools
- invoked_experts
- round / stop_reason / budget

不要再携带大量重复业务表示。

==================================================
十五、完成后输出
==================================================

修改完成后请给出：

1. 修改了哪些文件。
2. 删除了哪些旧概念/数据流。
3. 新的真实 RCA 数据流。
4. 哪些旧类/文件被删除或降级成内部实现。
5. Runtime 做了哪些最小适配。
6. 测试结果。
7. 如果仍存在你认为可以继续删除的冗余，请单独列出，但本任务不要擅自扩大范围。

最重要的要求：

这是一次“简化项目”的重构。

不要把旧复杂度换成一套新的复杂抽象。
如果两个设计都能实现需求，优先选：
更少的数据类型、更少的转换步骤、更少的规则、更容易解释的方案。