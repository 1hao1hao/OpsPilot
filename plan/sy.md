
---

# 任务 3：正式消融 + 性能指标 + EvalRAG 是否接入的决策

这个放最后执行。

最关键的是：**这一阶段仍然不自动把 EvalRAG 接进去。**

先跑结果，再判断。

```text
继续项目：

https://github.com/1hao1hao/OpsPilot

前提：

1. OpenTelemetry Astronomy Shop integration 已完成。
2. 6 Fault + Normal Benchmark 已完成。
3. 每个 scenario 可以稳定注入、采集 telemetry、执行 RCA、恢复。
4. Ground Truth 与 Agent 完全隔离。

本任务目标：

跑正式 RCA 实验和消融，
用真实结果决定系统价值与后续是否需要 Knowledge Tool。

不要先接 EvalRAG。

==================================================
一、固定 Release Benchmark
==================================================

冻结：

- OpenTelemetry Demo commit
- OpsPilot commit
- scenario dataset version
- workload config
- fault variants
- observation windows
- RCA config
- model / temperature（如果使用 LLM）

正式 Benchmark 以后：

不能跑一个 Case 改一次参数。

所有配置版本写入 artifacts。

==================================================
二、至少比较三种模式
==================================================

A. Full Adaptive

Coordinator
→ L1
→ Evidence / Gate
→ 证据不足
→ LLM Planner
→ 按需 Tool / Expert
→ RCA

必须是真 LLM Planner。

使用当前配置的模型。

不要把 deterministic fallback 结果叫 Full Adaptive。

记录每次：

LLM 是否真正使用
fallback 是否发生

--------------------------------------------------

B. Fixed Full Workflow

不使用 LLM Planner。

固定执行：

所有当前 RCA 所需要的 Tool 集合。

目标：

作为“完整观测但没有自适应决策”的 deterministic baseline。

它可以有较高 Tool Cost。

重点比较：

Adaptive 能不能：

用更少 Tool / LLM 调用
达到接近或更好的 RCA Accuracy。

--------------------------------------------------

C. Adaptive without L2 Expert

保持 Adaptive Planner，
但：

Expert budget = 0

用于验证：

L2 DB / Redis / Kafka / RPC Expert

到底有没有真实价值。

不要额外增加一堆 ablation。

第一版这三个足够。

==================================================
三、正式运行次数
==================================================

每个模式：

每个 scenario：

至少 3 repeats。

如果资源和 API 成本允许：

5 repeats。

但是：

不要为了凑数量伪造运行。

LLM 真实使用时：

必须记录 API 调用成功/失败/fallback。

==================================================
四、核心指标
==================================================

质量：

Top-1 Accuracy
Top-3 Accuracy
Normal / NO_FAULT Accuracy

可靠性：

Gate Pass Rate
Budget Exhaustion Rate
Degraded Rate

效率：

Avg Tool Calls
Avg Expert Calls
Avg Investigation Rounds
Avg LLM Calls

性能：

P50 Diagnosis Latency
P95 Diagnosis Latency

如果能从真实 LLM response 得到 usage：

Token Usage
Estimated Model Cost

可以加。

如果当前客户端拿不到：

不要为了这个大改架构。

==================================================
五、按场景报告
==================================================

必须同时输出：

overall

和：

per scenario。

特别是：

adHighCpu
paymentFailure
paymentUnreachable
kafkaQueueProblems
productCatalogLockContention
emailMemoryLeak
normal

例如不能：

Overall 85%

但隐藏：

Kafka 0/3。

==================================================
六、错误分类
==================================================

每个错误 Case 自动归类：

A. Telemetry 缺失
B. Tool adapter 错误
C. Evidence Builder 没提取出来
D. Ranking 错误
E. Gate 过早通过
F. Planner 选错下一步
G. Budget 不足
H. LLM 调用失败 / fallback
I. RootCause taxonomy 不够表达

生成：

failure_analysis.json
failure_analysis.md

不要让 LLM 随意猜失败原因。

优先根据：

ToolResults
Evidence
ActionHistory
GateDecision

确定。

无法确定时：

unknown。

==================================================
七、对 Adaptive 的真正判断
==================================================

最终必须回答：

Adaptive Planner 相比 Fixed Full：

1. Accuracy 是否下降？
2. Tool calls 降了多少？
3. Latency 是否下降？
4. Expert 是否真正被按需使用？
5. 哪些场景 Planner 有价值？
6. 哪些场景 fixed 更稳定？

不要为了简历故事强行说 Adaptive 更好。

如果实验结果是：

Fixed Full accuracy 更好，
Adaptive cost 更低，

就如实写成 trade-off。

==================================================
八、Knowledge Tool 决策，不直接开发
==================================================

接下来分析所有失败 Case：

问：

“这个错误是否因为缺少历史故障知识 / Runbook / 架构知识？”

而不是：

“怎么才能把 EvalRAG 塞进来？”

定义明确判断标准。

只有满足下面条件，才建议下一阶段接 Knowledge Tool：

至少满足：

1. 存在多个真实失败 Case，不只是一个偶然 Case；
2. Metrics / Logs / Traces 已经正常采到；
3. 实时 Evidence 无法区分多个候选根因；
4. 历史故障案例 / Runbook / service dependency 文档中确实存在可以区分这些候选的信息；
5. 问题不是简单 Tool bug / Evidence bug / Planner bug。

例如：

Telemetry 告诉我们：

DB latency elevated

但无法区分：

lock contention
vs
known slow-query pattern

而 Runbook / 历史 incident 有明确模式。

这才属于 Knowledge Tool 的合理价值。

==================================================
九、生成 Knowledge Tool Decision Report
==================================================

输出：

artifacts/knowledge_tool_decision.md

必须明确给出三个结果之一：

A. NOT_NEEDED

实时 telemetry 已足够，
接 RAG 只会增加复杂度。

B. JUSTIFIED

存在明确失败 Case，
历史知识有增量价值。

C. INCONCLUSIVE

当前问题首先是 telemetry / benchmark 不完善，
暂时不能判断。

报告包含：

- 哪些 Case
- 当前 Evidence
- 当前错误原因
- 什么外部知识可能帮助
- 为什么实时 telemetry 不够
- 如何验证 Knowledge Tool 是否真的提升

禁止只写：

“RAG 可以增强系统知识”。

==================================================
十、如果结论 JUSTIFIED
==================================================

仍然不要在本任务直接修改 EvalRAG 或接 Knowledge Tool。

只生成下一阶段设计草案：

Knowledge Tool 输入：

service
symptom
current evidence

查询：

historical incidents
runbooks
service docs

输出：

KnowledgeEvidence

再进入：

同一个 Evidence Pool

必须继续遵守：

LLM/RAG 不直接决定 RootCause。

然后给出下一阶段实验：

Without Knowledge
vs
With Knowledge

比较：

Top1 / Top3
Tool Cost
Latency

只有有真实提升才保留。

==================================================
十一、如果结论 NOT_NEEDED
==================================================

明确写：

不要为了技术栈丰富强行接 EvalRAG。

EvalRAG 继续作为独立简历项目。

OpsPilot 保持：

Telemetry-driven RCA。

==================================================
十二、实验报告
==================================================

生成正式：

reports/otel_demo_rca_v1.md

至少：

1. Environment
2. Fault Dataset
3. Modes
4. Metrics
5. Per-scenario results
6. Overall results
7. Adaptive vs Fixed
8. L2 Expert ablation
9. Failure analysis
10. Limitations
11. Knowledge Tool decision

所有表格数字：

必须从机器生成 artifact 读取。

禁止手写一个“漂亮数字”。

==================================================
十三、README 更新
==================================================

如果正式实验成功：

README 可以加入：

“在 OpenTelemetry 官方 Astronomy Shop
近真实微服务环境上构建 6 类故障 + normal Benchmark。”

随后只引用真实结果。

例如未来真实结果如果是：

Top1 = X
Top3 = Y
Adaptive tool calls -Z%

才能写。

不要预先写目标数字。

==================================================
十四、简历数据草稿
==================================================

生成：

artifacts/resume_evidence.md

不是最终简历文案。

只整理所有可以真实引用的数字：

- dataset
- Top1/Top3
- Tool 调用降低
- Latency
- Recovery
- Normal accuracy
- upstream environment

每条必须附：

artifact path / 实验配置

以后人工写简历时可以追溯。

==================================================
十五、完成后输出
==================================================

最终总结：

1. Full Adaptive 真实结果。
2. Fixed Full 真实结果。
3. No-L2 结果。
4. 哪种方案 Accuracy 最高。
5. 哪种成本最低。
6. Adaptive 的真实收益 / 代价。
7. 最大失败来源。
8. Knowledge Tool Decision：
   NOT_NEEDED / JUSTIFIED / INCONCLUSIVE。
9. 如果 JUSTIFIED，列下一阶段计划，但不要实现。
10. 所有实验 artifact 路径。

最重要：

不要为了让项目看起来优秀而修改实验结论。
负结果同样保留。

```

## 执行记录（2026-10-05，Asia/Shanghai）

状态：正式实验运行中，尚未完成，不提前宣称结果。

前置 task1.5 及第一版 6 Fault + Normal 真实 Benchmark 已完成。新增正式实验配置 experiment.yaml、真实模式适配 modes.py、配对生命周期 experiment.py、机器统计/错误分析 experiment_report.py 和回归测试；复现入口 benchmarks/datasets/otel_demo/v1/EXPERIMENT.md。

真实预检发现 DeepSeek JSON 模式 HTTP 400：通用客户端没有在提示词中声明 JSON。仅补充 JSON 输出声明并验证官方接口要求；未改 Planner 选择、fallback、Gate、Ranker 或 telemetry adapter。修复后 Full 与 No-L2 各 3 次真实 API/Planner 决策成功、无 fallback，预检不计入正式次数。预检全套测试 263 passed / 4 skipped，Ruff 通过。工件 artifacts/otel_demo_rca_v1_preflight/。

正式运行：artifacts/otel_demo_rca_v1/20261005T032959Z-d57607/，日志 artifacts/otel_demo_rca_v1_release.txt。固定原 Demo commit、OpsPilot HEAD + 未提交源码快照、dataset 1.0.1、experiment 1.0.0、Locust 5 用户/spawn=1、release 时间参数、模型 deepseek-v4-flash/temperature=0。冻结源码和配置哈希，每个周期检查；不逐 Case 调参。

计划真实执行 7 scenarios × 3 repeats × 3 modes = 63 次独立 RCA，配对共享同一固定 Alert 时间窗，按 repeat 轮换三种模式顺序，共 21 个注入/恢复周期。Fixed 实际运行全部 13 工具，无 LLM、无 Expert 调用；No-L2 保持真实 Planner，只将 Expert budget 设为 0。原始 API response/usage、有效决策、fallback、调用/证据/候选/Gate 分别保存。失败和重试不伪造重复次数。

正式结果须等全部生命周期与恢复结束后生成 reports/otel_demo_rca_v1.md、failure_analysis.json/md、artifacts/knowledge_tool_decision.md、artifacts/resume_evidence.md；当前 reports/otel_demo_rca_v1_progress.md 仅为进度快照。尚未实施 EvalRAG 或 Knowledge Tool。

更新：1.0.0 于 CPU 第一轮恢复检查中安全中止，保存 12 次实际 RCA，未继续注入、故障全部 off。Docker 和 JVM 已空闲，但原 container utilization gauge 仍偏高，未达到冻结恢复阈值；不放宽阈值、不合并旧分数。报告 reports/otel_demo_rca_v1_interrupted.md，诊断与独立验证 artifacts/otel_demo_rca_v1_preflight/cpu_*。仅修复通用 CPU 指标，统一优先 scoped CPU 时间计数器的一分钟 rate / 1e9；新增匿名 problempattern 控制日志过滤及恢复失败 fixture 标记。真实验证 CPU 0.00820755→1.0→0.00770254，恢复通过；全套测试 266 passed / 4 skipped，Ruff 通过。

当前正式运行改为 experiment 1.0.1：artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16/，日志 artifacts/otel_demo_rca_v1_release_verified.txt，从 7×3×3 完整重跑。其余负载、window、fault variants、model、RCA budgets 和 dataset mapping 不变，新源码/配置重新冻结，仍未完成，不提前宣称正式结果。

续跑记录：进程 PID 31200 消失，原因未证实。最初日志落后于生命周期 JSON，曾误判 CPU 第一组未完成；复核确认第一组已诊断且成功恢复，真正中断的是第二组，第二组尚无 RCA score。已完成结果不重跑，原始 telemetry 和控制历史保存在 resume_history/process_exit_01/。全部故障关闭并通过 flagd 确认后，使用相同冻结配置 --resume 启动独立后台进程，仅重试 CPU 第二组。日志 artifacts/otel_demo_rca_v1_resume01.log 与 *_error.log。中断索引的重复条目将按已保存 score / recovery JSON 更正并归档旧索引，不计作额外诊断。

## 最终完成记录（2026-10-05，Asia/Shanghai）

状态：已完成正式 release 及任务要求的三模式比较、失败分析、Knowledge 决策、README 更新和可追溯数字草稿。最终运行 `artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16`，experiment 1.0.1，dataset 1.0.1。

真实 63 次有效 RCA、21 个完整配对生命周期，6 类故障每类 3 次均确认产生效果与恢复；7×3×3 矩阵和冻结/泄漏/调用计数独立检查通过。最后 28 个服务运行、全部 fault off、upstream clean。

| 模式 | Fault Top1 | Fault Top3 | Normal | Avg Tool | Avg Expert | P50 / P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| full_adaptive | 10/18 | 12/18 | 2/3 | 6.10 | 1.00 | 17038.33 / 21673.26 |
| fixed_full | 10/18 | 11/18 | 0/3 | 13.00 | 0.00 | 27408.37 / 28744.40 |
| adaptive_no_l2 | 10/18 | 12/18 | 2/3 | 5.71 | 0.00 | 13809.91 / 14738.43 |

Full 工具调用比 Fixed 少 53.11%；最大观测错误条件计数 `{'G': 8, 'unknown': 3, 'C': 3, 'F': 6, 'D': 9}`，不据此声称唯一因果。真实 LLM/fallback 逐次明细、Token、Gate/Budget/Degraded 和 L2 消融在最终报告及 metrics.json；现金成本 N/A。

Knowledge Tool Decision：INCONCLUSIVE。缺少经核验的独立历史 incident/runbook 且基础 pipeline 问题未排除，未实现 EvalRAG/Knowledge Tool。

完整报告 reports/otel_demo_rca_v1.md；自动错误分类 artifacts/failure_analysis.json/md；知识决策 artifacts/knowledge_tool_decision.md；数字草稿 artifacts/resume_evidence.md。每个 mode 的 raw requests/responses/usage/tool/evidence/score 和每个 trial 的 baseline/fault/recovery 均保留。

1.0.0 CPU gauge 恢复失败现场单独保留，不合并正式得分。1.0.1 进程中断发生于 CPU 第二组，尚无 RCA；CPU 第一组已完成恢复。曾因落后日志误建的重复索引保存在 resume_history/process_exit_01/*before_index_correction.json，并依据生命周期 JSON 更正，不增加真实诊断数。

最终本地测试 273 passed / 4 skipped；Ruff 通过。验证记录 `artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16/final_verification.json`。Planner/Fallback、Evidence Gate、Ranker 未为本轮结果调参；旧 Benchmark 6/21 未处理。
