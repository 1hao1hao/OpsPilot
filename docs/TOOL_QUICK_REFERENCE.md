# 工具速查

更新：2026-10-12。当前 13 个工具均只读。

| 类别 | 工具 |
| --- | --- |
| General | metrics.query、logs.query、traces.query、changes.query、topology.query、alerts.query |
| DB | db.replication、db.slowlog、db.connections |
| Redis | redis.memory、redis.hotkeys |
| Kafka | kafka.lag |
| RPC | rpc.metrics |

Seed 只调用 General；Planner `inspect_tool` 也仅允许 General，领域通过 `invoke_expert`。没有 `*.inspect` 兼容工具或 finalize 动作。Fixed 真实实验调用全部 13 个工具，No-L2 关闭 Expert。

工具负责观测，Evidence/算法负责事实与候选，`decide_diagnosis` 负责最终结论。成功返回空不代表健康，预算用完不代表根因确认。完整参数、领域语义与范围限制见 [工具说明](tools_intro.md)，调查顺序见 [数据流](RCA_DATAFLOW_REFERENCE.md)。
