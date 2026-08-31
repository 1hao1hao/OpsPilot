```
请基于当前 main 分支继续重构 OpsPilot。本轮目标不是增加更多功能，
而是让 Adaptive RCA 架构严格统一成一条容易理解、容易解释的数据流。

重要原则：
1. 不根据现有代码勉强兼容错误设计；优先让代码符合下面定义的架构。
2. 保留现有 Runtime、Checkpoint、幂等、Mock Environment、L1/L2 分析逻辑和 RCA 算法实现。
3. 不增加 Memory、Self-Consistency、复杂 RAG 等额外模块。
4. 完成后必须更新测试与 README，但暂时不要生成学习说明书。

==================================================
一、最终目标架构
==================================================

必须实现：

Alert
→ Seed Planner
→ Tool execution
→ ToolResult
→ L1 / selected L2 analysis
→ Deterministic Evidence Engine
    - Raw Tool Evidence
    - L1 Evidence
    - L2 Expert Evidence
    - AlgorithmSignal: MetricFilter/NoiseFilter/WoW-DoD/IQR/Volatility
    - ExpertRuleEngine
→ 完整 Evidence Pool
→ RootCause Ranking
→ Evidence Gate
    - sufficient → stop investigation
    - budget exhausted → stop investigation
    - insufficient → LLM Adaptive Planner
→ Planner chooses next Tool or Domain Expert
→ execute
→ next round

循环结束后：
直接把最后一轮 Evidence Pool + RootCause Ranking 作为最终结果，
不要重新构造另一套独立 Ranking 流程。

Optional constrained LLM explanation 仍然只负责解释最终 Top1。

==================================================
二、修复当前“Provisional Ranking 在 L3 之前”的分裂设计
==================================================

当前 investigation.engine 中：
Raw/L1/L2 Evidence → provisional ranking → Gate

而 runtime.execution 中：
调查结束 → run_deterministic_pipeline → 加 Algorithm/Rule Evidence → 再 Ranking

删除这种阶段分裂。

新增/重构一个统一的 DeterministicEvidenceEngine（名称可合理调整）。

每一轮 Tool 执行结束后统一执行：

1. analyze_dimensions()
2. analyze_experts(selected domains)
3. collect_evidence()
4. collect_semantic_evidence()
5. collect_expert_evidence()
6. MetricFilter + NoiseFilter
7. WoW / DoD
8. IQR
9. Volatility
10. ExpertRuleEngine
11. AlgorithmSignal / Rule → Evidence
12. Evidence 去重
13. RootCauseAgent deterministic ranking

输出统一 RoundAnalysisResult，至少包含：

dimension_results
expert_results
algorithm_signals
matched_rules
evidence
candidates

Evidence Gate 必须基于这个“完整 Evidence Pool + candidates”。

runtime.execution 不应再在 investigation 结束后重复跑一次 deterministic pipeline。

==================================================
三、Adaptive Planner 改为真正的 LLM Planner
==================================================

当前 AdaptivePlanner 是关键词规则 Planner，请改为：

LLMAdaptivePlanner
+
DeterministicPlannerFallback

正常模式：
如果 OPSPILOT_LLM_ENABLED=true，
Adaptive Planner 使用 DeepSeek 做调查决策。

LLM Planner 输入只能包含：

- Alert 的公开字段：
  service_name
  alert_type
  severity
  description
  labels

- 当前 L1 Findings 摘要
- 当前 L2 Findings 摘要
- 当前 Evidence 摘要
- 当前 provisional Top-K（root cause type + score/confidence 即可）
- 已执行 Tool
- 已调用 Expert
- action history
- remaining round/tool/expert budget
- allowed actions

绝对禁止直接读取 alert.signals。
alert.signals 是 Tool backend / benchmark observation snapshot，
Planner 不允许绕过 Tool 查看。

Planner 输出严格结构化，只允许：

{
  "action": "inspect_tool" | "invoke_expert",
  "target": "...",
  "reason": "..."
}

Adaptive Planner 不再输出 FINALIZE。

停止调查只允许由：
1. Evidence Gate sufficient
2. budget exhausted
3. 没有任何合法的新 Action
触发。

这样避免 Planner finalize 和 Evidence Gate 双重停止逻辑。

如果：
- LLM disabled
- timeout
- API error
- JSON/schema invalid
- target 不在 whitelist

则使用 deterministic fallback Planner。

Fallback 只作为可靠性兜底，不要再增加“规则置信度→决定是否调用LLM”的复杂逻辑。

==================================================
四、Planner / Expert Tool 权限边界
==================================================

当前所有 Domain Tool 都注册在同一个 Registry，
代码层面任何调用方理论上都能执行。

请增加明确的 Tool metadata / action validation，使：

Seed Planner：
只能选择 seed/general observation tools。

Adaptive Planner：
只能直接选择 general tools，例如：
metrics.query
logs.query
changes.query
traces.query
topology.query
alerts.query

Adaptive Planner 不允许直接调用：
db.replication
db.slowlog
db.connections
redis.memory
redis.hotkeys
kafka.lag
rpc.metrics

这些 Domain Tools 只能通过：

invoke_expert("db")
invoke_expert("redis")
invoke_expert("kafka")
invoke_expert("rpc")

后由对应 Expert 选择。

需要中央 validator 强制执行，
不要只依赖 Planner 自觉。

==================================================
五、Dynamic L2 Expert
==================================================

保留四个 Domain Expert：

DB
Redis
Kafka
RPC

Planner 只决定 invoke_expert(domain)。

Expert 根据当前：
Alert
+ L1 Findings
+ 已有 ToolResult / Evidence

选择自己的领域 Tool。

例如 DB：

db.replication
db.slowlog
db.connections

不能固定全部调用，要按上下文选择必要 Tool。

Expert ToolResult
→ L2 Finding
→ collect_expert_evidence()
→ 正式进入 Evidence Pool。

==================================================
六、Evidence Gate 的“独立来源”修复
==================================================

当前代码使用 evidence.source_name 去重，
这会把：

db.replication Tool Evidence
和
基于同一个 db.replication ToolResult 得出的 db_expert Evidence

错误计算成两个“独立来源”。

请增加明确 provenance/source_group 概念。

目标：

同一个底层 Observation 派生出的：

Tool Evidence
Finding Evidence
Algorithm Evidence

必须继承同一个 observation provenance，
不能因为 source_name 不同就算多个独立来源。

例如：

db.replication ToolResult
→ raw Evidence
→ DB Finding
→ Expert Evidence

它们的 source_group 应相同，例如：
tool:db.replication:<tool_call_id>

因此 Evidence Gate 的 independent_source_count
统计的是不同 source_group，而不是 source_name。

对于 Metrics：

metrics.query
→ L1 cpu finding
→ IQR AlgorithmSignal
→ algorithm Evidence

如果全部来自同一 metrics.query observation，
也只能算一个独立 observation source。

Trace / Logs / DB replication 等真正不同的 Observation
才能算多个独立来源。

保留 Gate 三条件：

Top1 confidence >= configured threshold
AND
Top1 - Top2 margin >= configured margin
AND
支持 Top1 的 independent source_group >= configured min_sources

==================================================
七、RootCause Ranking
==================================================

RootCauseAgent 保持一套确定性算法。

每轮 Evidence Engine 调用一次：

Evidence Pool
→ RootCauseAgent.diagnose()
→ provisional candidates

Evidence Gate 使用 provisional candidates。

调查停止后：

最后一轮 provisional candidates
直接成为 final deterministic candidates。

不要在 Runtime 后面再次重新 Ranking，
除非 Evidence 又发生了变化。

命名/代码结构要明确表达：

same ranking algorithm
+
different investigation round

而不是两套 Ranking。

==================================================
八、Budget
==================================================

继续保留简单的三种硬预算：

max_rounds
max_tool_calls
max_expert_calls

不要新增复杂 token cost / confidence cost 模型。

达到任意 hard limit：
budget_exhausted=True
→ 停止 Adaptive Investigation
→ 使用当前完整 Evidence Pool 的 Ranking 作为结果。

==================================================
九、删除/避免冗余
==================================================

重点检查并删除以下冗余：

1. Engine 先判断 gate.sufficient，
   Planner 内部又判断 gate.sufficient。

2. Planner FINALIZE Action 与 Evidence Gate 重复。

3. Investigation provisional ranking 和 Runtime 最终 L3 ranking 分裂。

4. Planner 直接读取 alert.signals。

5. source_name 被当成 independent evidence source。

6. 相同底层 Observation 通过 Tool/L1/L2/Algorithm 多次派生 Evidence
   时造成 Gate 虚假多源支持。

==================================================
十、期望的数据流示例
==================================================

timeout Alert
↓
Seed Planner
→ metrics.query
→ traces.query
→ changes.query

↓ Round Analysis

L1:
trace path order → inventory → mysql is slow

Algorithms:
tp99 IQR anomaly

Evidence Pool:
trace anomaly evidence
metric anomaly evidence

Ranking:
RPC_TIMEOUT Top1

Evidence Gate:
confidence/margin/source diversity 不足
→ continue

LLM Adaptive Planner 输入：
Alert
+ findings
+ evidence summary
+ provisional ranking
+ action history
+ budget

LLM输出：
invoke_expert("db")
reason:
"slow trace terminates at mysql; DB-specific evidence is missing"

↓
DB Expert

选择：
db.replication
db.slowlog

↓ Round Analysis重新执行

L2:
db_replication_lag

完整 Evidence：
trace
metrics algorithm
DB replication

Ranking:
DB_REPLICATION_LAG Top1

Gate：
confidence >= threshold
margin >= threshold
independent observation sources >= threshold

→ STOP

最后一轮：
Evidence Pool + Ranking
直接作为最终确定性结果

→ optional constrained LLM explanation
→ Report

==================================================
十一、验收
==================================================

完成代码后：

1. 跑完整测试。
2. 修复所有 regression。
3. 增加测试证明：
   - Planner 不可读取 alert.signals
   - Planner 不可直接调用 Domain Tool
   - LLM Planner invalid output 会 fallback
   - 同一 ToolResult 派生的多份 Evidence 只算一个 independent source
   - 不同 Observation 能正确计为多 source
   - Algorithm Evidence 在每轮 Gate 之前已经存在
   - Gate 使用完整 Evidence Pool
   - 调查结束后不会重复执行 L3 / Ranking
   - Worker checkpoint/recovery 仍然工作
4. 输出：
   - 修改文件列表
   - 新主数据流
   - 测试结果
   - 与本需求仍存在的偏差（如有）

不要生成项目学习说明书。
等这一轮架构稳定后再单独整理说明书。
```