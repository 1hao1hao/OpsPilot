# OpsPilot 工具与调查链

更新：2026-10-12。以 `tools/registry.py`、`investigation/planner.py` 和 `decision.py` 为准；[旧说明](history/20261012/tools_intro.md) 仅用于追溯。

所有工具输入为 `AlertToolInput(alert: AlertEvent)`，handler 返回 `ObservationOutput`，Executor 包装为 `ToolResult.data.observations`。默认 Registry 为 **6 个 General + 7 个 Domain，共 13 个只读工具**，没有 `db.inspect`、`redis.inspect`、`kafka.inspect` 或 `rpc.inspect`。

## 工具清单

| Tool | 类别 | 观测与证据用途 |
| --- | --- | --- |
| `metrics.query` | General | QPS、延迟、错误率、CPU/内存等；collector 与 AnomalyDetector 生成资源和算法 Evidence |
| `logs.query` | General | 结构化日志；OOM 签名可产生证据，三终态还检查服务与事件时间 |
| `traces.query` | General | 归一化 spans、父子关系、耗时和状态；服务范围、失败调用及数据库延迟群组 |
| `changes.query` | General | 发布/配置变化上下文；变化本身不等于已确认故障 |
| `topology.query` | General | 依赖上下文；没有独立根因票据 |
| `alerts.query` | General | 相关告警/问题上下文；不能代替当前告警的直接观测 |
| `db.replication` | DB Expert | 复制延迟；现有 collector 的绝对阈值为 5 秒 |
| `db.slowlog` | DB Expert | 慢查询及群组；存在 cohort 时只使用被 corroborated 的慢调用统计 |
| `db.connections` | DB Expert | active/max connections；现有连接使用率阈值 80% |
| `redis.memory` | Redis Expert | 内存及容量；现有内存使用率阈值 80% |
| `redis.hotkeys` | Redis Expert | 命中率与热点上下文；带时间历史时要求实际下降，而非只凭低值 |
| `kafka.lag` | Kafka Expert | consumer lag；带时间上下文时要求实质变化，不能只凭积存绝对值 |
| `rpc.metrics` | RPC Expert | 调用量、timeout/error rate、延迟和基线；缺少协议结果保持未知 |

RPC 明确观测到低 timeout rate 时，不从延迟比直接宣称 timeout；仅缺少 timeout 字段的历史路径保留延迟比推断。数据库慢调用对实时数据要求告警时间附近同类调用 corroboration：至少 3 次慢调用且群组平均耗时超过原 1 秒预算。孤立慢调用保留线索，不自动获得同等因果票据。

阈值产生候选 Evidence，不等于最终确认。`ToolStatus.SUCCESS` 只表示调用成功；空结果不表示健康。需要可信健康参考、范围与覆盖才能得到 `NO_FAULT`。工具 ERROR 记录 error_code，并使报告 degraded；成功但缺少字段也可能 `INCONCLUSIVE`。

## 首轮与后续动作

| Alert type | 默认 Seed |
| --- | --- |
| timeout | metrics、traces、changes |
| error_rate | metrics、logs、traces |
| resource | metrics、logs |
| custom | metrics、logs、changes |

严重程度和 labels 可调整 Seed 顺序，生产默认最多 3 个。后续 Planner 仅能 `inspect_tool` 调通用工具或 `invoke_expert` 调领域；`finalize` 不属于动作枚举。Expert 根据已支持根因选择领域工具；没有细分支持时使用本领域小型工具集。

每轮执行 Tool → Evidence/算法 → Top-K → Gate/动态需求/Fault Presence → Verdict。未满足需求传给 Planner；预算耗尽或无合法动作时保留未确认候选。默认预算和 API 契约见 [README](../README.md)。Fixed 实验模式执行同一 Registry 全部 13 个工具，不调用 Planner；No-L2 禁用 Expert，不能冒充领域测量。

Mock 可从 `alert.signals` 读取测试后端快照，但 Planner 不可读取它；真实 OpenTelemetry 请求的告警只含公开症状。Ground Truth、注入 flag 和答案不得进入在线决策。后端字段与已知缺口见 [集成指南](../integrations/opentelemetry_demo/README.md)。
