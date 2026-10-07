Repository:
https://github.com/1hao1hao/OpsPilot

目标：完成 OpsPilot 最后一轮 RCA Accuracy Closure。

当前 Astronomy Shop 正式实验中：

- Fault Top1 = 10/18
- 主要失败集中于 Kafka backlog、Database latency、Memory growth
- Normal 也存在 RPC timeout / Redis low hit rate 假阳性

本任务请一次性完成分析、修改、实验和收尾，不要等待我逐步确认。

## 验收目标

在**重新产生的新一轮 Astronomy Shop 故障生命周期**上：

- Fault Top1 ≥ **17/18（94.4%）**
- Normal NO_FAULT ≥ **2/3，优先 3/3**
- 不允许通过增加工具全量调用来换准确率；Adaptive 平均 Tool Call 应继续明显低于 Fixed Full
- 不允许降低评分标准或修改 Ground Truth

如果经过合理的通用改进仍无法达到目标，不得伪造结果；保留最佳真实结果并明确解释剩余瓶颈。

---

## 1. 先利用现有失败工件定位通用问题

重点检查当前已知问题：

### Kafka
历史实际 lag 为约 606~808，但 Evidence 使用固定 `lag >= 1000` 阈值导致 Evidence 丢失。

不要针对 `kafkaQueueProblems` 写特判。

改为更合理的 baseline-aware / temporal anomaly：

- 当前值相对历史 baseline 的增长
- 趋势或变化幅度
- 必要时结合绝对值作为辅助信号

### Database
历史 Fixed 已采集到 `db.slowlog` 并生成 `DB_SLOW_QUERY` Evidence，但正确原因仍被 RPC / resource symptom 压过。

检查 RootCause Ranker 当前简单 Evidence confidence 累加是否合理。

引入通用的 Evidence specificity / causal proximity：

- 直接领域证据应比下游二次症状更有区分度
- RPC timeout/error 等泛化症状不能因为数量多就轻易压过明确 DB/Kafka/Resource 证据
- 独立来源可以增强可信度，但同源重复观测不能无限叠加

### Memory
`resource_saturation` 多次已经进入 Top-K，却被 RPC symptom 排到第二。

与 Database 使用同一套通用 ranking 原则解决，不写 memory 场景特判。

### Normal
检查：

- “慢 span”是否被错误等同于真正 RPC timeout
- Redis 绝对低 hit rate 是否缺少 baseline 差异
- 稳定存在的低值/慢值是否被错误识别成故障

原则：

> 异常应尽量依赖“相对正常基线发生变化”，而不是单一绝对阈值。

---

## 2. 修改范围

优先限制在：

- Observation → Evidence 转换
- baseline / temporal anomaly detection
- Evidence confidence / specificity
- RootCause ranking
- 必要的 Evidence Gate 配合调整

不要重构：

- Multi-Agent 架构
- Runtime
- PostgreSQL / Redis Worker
- Astronomy Shop integration
- API

只有确有证据表明 Planner 未采集必要的通用诊断信息时，才允许做最小的通用 Planner 改进。

---

## 3. 自主迭代，不要等待用户确认

在现有历史运行上做 failure analysis 和开发验证。

允许你自行进行最多 **3 轮通用规则迭代**：

```text
分析失败
→ 修改通用 Evidence / Ranker
→ regression / replay
→ 检查是否引入新假阳性
```

每一轮保存 before / after 和改动原因。

不要为了达到目标无限调参。

---

## 4. 最终必须 fresh rerun

最终规则冻结后，重新运行完整：

- 6 Fault + Normal
- each × 3 repeats
- 至少 Full Adaptive
- 同时跑 Fixed Full 作为质量/工具成本对照

必须重新执行真实 fault lifecycle：

baseline
→ enable fault
→ stabilization
→ symptom-only Alert
→ RCA
→ reset
→ recovery

不能只拿旧的 ToolResult 离线重排后把它算成新的正式准确率。

如果时间成本允许，No-L2 可以一起重跑；它不是本任务核心验收条件。

---

## 5. 最终输出

明确给出：

### Accuracy
- Full Adaptive Fault Top1：x/18
- Fault Top3：x/18
- Normal：x/3
- 每个场景 Top1

### Efficiency
- Avg Tool Calls
- P50 / P95
- 与 Fixed Full 对比

### Before / After
重点比较：

- 原 Top1：10/18
- 新 Top1：x/18
- Kafka：0/3 → ?
- Database：0/3 → ?
- Memory：1/3 → ?
- Normal：2/3 → ?

### Changes
只总结真正改变结果的 3~5 个通用机制，例如：

- baseline-aware anomaly
- Evidence specificity
- symptom discounting
- independent-source weighting

不要把大量文件改动当成果。

### Integrity
确认：

- 无 scenario 特判
- 无 Ground Truth leakage
- benchmark/scorer 未修改以提高分数
- fresh lifecycle rerun
- tests / Ruff 通过

如果达到 Fault Top1 ≥17/18，冻结配置并更新 README / 正式报告。

如果未达到，输出：

`ACCURACY_TARGET_NOT_MET`

以及真实最佳结果和剩余根因，不得制造 ≥90% 的结论。

最后 commit 并 push 当前修改，返回 commit SHA。
## Completion record (2026-10-08)

**ACCURACY_TARGET_NOT_MET**. Completed three general rule iterations, fresh release and independent verification. Full Fault Top1 16/18; Top3 18/18; Normal 2/3; tools 5.67 versus Fixed 13.00. See `reports/accuracy_closure.md` and `artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e/final_verification.json`. Failed targets are reported honestly; no additional rule tuning after freeze.
