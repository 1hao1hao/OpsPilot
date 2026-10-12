# Fixed Investigation vs Adaptive Investigation 对照实验

> 历史实验报告：描述当时版本的 snapshot 调查与 L2 Finding promotion，非当前三终态实现。原指标保留供追溯；最新架构与验证范围见 [项目 README](../README.md)。

实验日期：2026-09-11。代码基线：`2c3d0a581ff6a497621a927b28646ee3f937d139`。数据集：`opspilot-rca@1.0.0`。

## 1. 实验问题与公平性

目标：验证 Adaptive Investigation 能否在不降低 RCA 质量的前提下，减少 Fixed Investigation 的无关 Tool/Expert 调用。

| 对照系统 | 实际行为 | 用途 |
|---|---|---|
| A. `opspilot_fixed_planner` | 每例固定并发调用 6 个 General + 4 个 legacy inspect Tool，并运行 4 个 Expert | 全量调查 baseline |
| B. `opspilot_full_adaptive` | 告警感知 Seed；Gate 不足时动态选择 General Tool 或 Expert；提升 L2 Finding Evidence | 完整方案 |
| C. `opspilot_adaptive_planner` | 与 B 使用相同动态调查，但不把 L2 Expert Finding 提升为 Evidence；Domain Tool raw Evidence 仍保留 | L2 Finding promotion 消融 |
| D. `opspilot_adaptive_without_dynamic_l2` | `max_expert_calls=0`，首轮 Seed 后即预算耗尽，不调用 Expert/Domain Tool | Dynamic L2 整体消融 |

公平性约束：

- 四组使用相同数据集、split、当前代码和评价函数；dev 25 例，frozen-test 12 例。
- 所有命令显式设置 `OPSPILOT_LLM_ENABLED=false`；八个工件的模型 API calls 均为 0。
- 没有修改 benchmark、threshold、系统实现或评价逻辑。
- dev 与 frozen-test 分开报告；未根据 frozen-test 调参。
- manifest 均为 `git_dirty=true`，原因是运行前已有文档改动和未跟踪工件；八组在同一工作树连续执行，期间没有修改代码。

## 2. 运行命令与验证

```bash
OPSPILOT_LLM_ENABLED=false PYTHONPATH=src \
python -m opspilot.evaluation.cli run --config <CONFIG> --split <dev|test>
```

`<CONFIG>` 分别使用：

- `benchmarks/configs/fixed_planner.yaml`
- `benchmarks/configs/adaptive_planner.yaml`
- `benchmarks/configs/adaptive_without_dynamic_l2.yaml`
- `benchmarks/configs/full_adaptive_rca.yaml`

评价代码回归验证：

```text
PYTHONPATH=src pytest -q tests/evaluation
15 passed in 1.24s
```

## 3. dev 结果

### 3.1 诊断质量

| System | Hit@1 | Hit@3 | Evidence Recall | FPR | 成功 case |
|---|---:|---:|---:|---:|---:|
| Fixed | 1.000 (21/21) | 1.000 (21/21) | 1.000 | 0.000 (0/4) | 25/25 |
| Adaptive Planner | 1.000 (21/21) | 1.000 (21/21) | 1.000 | 0.000 (0/4) | 25/25 |
| Without Dynamic L2 | 0.190 (4/21) | 0.238 (5/21) | 0.190 | 0.000 (0/4) | 25/25 |
| Full Adaptive | 1.000 (21/21) | 1.000 (21/21) | 1.000 | 0.000 (0/4) | 25/25 |

### 3.2 调查开销

| System | Avg Tools | Avg Experts | Avg Rounds | Avg latency* | P95 latency* | Budget exhausted |
|---|---:|---:|---:|---:|---:|---:|
| Fixed | 10.00 | 4.00 | 1.00 | 0.578 ms | 0.739 ms | 0/25 |
| Adaptive Planner | 4.96 | 1.96 | 3.00 | 0.883 ms | 1.194 ms | 24/25 |
| Without Dynamic L2 | 2.76 | 0.00 | 1.00 | 0.372 ms | 0.465 ms | 25/25 |
| Full Adaptive | 4.96 | 1.96 | 3.00 | 0.900 ms | 1.213 ms | 24/25 |

\* latency 是 snapshot 数据上的进程内耗时，不包含 PostgreSQL、Redis、网络 Mock、真实监控系统或 LLM。它只能说明当前 evaluator 中 Adaptive 多轮重算比 Fixed 单轮并发更慢，不能代表线上时延。

Full Adaptive 相比 Fixed：

- Avg Tools：`10.00 → 4.96`，减少 **50.4%**。
- Avg Experts：`4.00 → 1.96`，减少 **51.0%**。
- Avg Rounds：`1.00 → 3.00`，增加 2 轮（+200%）。
- Hit@1、Hit@3、Evidence Recall、FPR 均无下降。

## 4. frozen-test 结果

### 4.1 诊断质量

| System | Hit@1 | Hit@3 | Evidence Recall | FPR | 成功 case |
|---|---:|---:|---:|---:|---:|
| Fixed | 1.000 (10/10) | 1.000 (10/10) | 1.000 | 0.000 (0/2) | 12/12 |
| Adaptive Planner | 1.000 (10/10) | 1.000 (10/10) | 1.000 | 0.000 (0/2) | 12/12 |
| Without Dynamic L2 | 0.100 (1/10) | 0.100 (1/10) | 0.100 | 0.000 (0/2) | 12/12 |
| Full Adaptive | 1.000 (10/10) | 1.000 (10/10) | 1.000 | 0.000 (0/2) | 12/12 |

### 4.2 调查开销

| System | Avg Tools | Avg Experts | Avg Rounds | Avg latency* | P95 latency* | Budget exhausted |
|---|---:|---:|---:|---:|---:|---:|
| Fixed | 10.00 | 4.00 | 1.00 | 0.592 ms | 1.211 ms | 0/12 |
| Adaptive Planner | 4.83 | 2.00 | 3.08 | 0.933 ms | 1.722 ms | 12/12 |
| Without Dynamic L2 | 2.67 | 0.00 | 1.00 | 0.416 ms | 1.050 ms | 12/12 |
| Full Adaptive | 4.83 | 2.00 | 3.08 | 0.965 ms | 1.786 ms | 12/12 |

Full Adaptive 相比 Fixed：

- Avg Tools：`10.00 → 4.83`，减少 **51.7%**。
- Avg Experts：`4.00 → 2.00`，减少 **50.0%**。
- Avg Rounds：`1.00 → 3.08`，增加约 2.08 轮（+208.3%）。
- Hit@1、Hit@3、Evidence Recall、FPR 均无下降。

## 5. 按根因类型拆分（dev）

Fixed 每类都是 10 Tools、4 Experts、1 Round。下表展示 Full Adaptive；因此所有类型的 Tool 调用都少于 Fixed，但调查轮数更多。

| Root cause | Cases | Avg Tools | Tool 减少 | Avg Experts | Avg Rounds |
|---|---:|---:|---:|---:|---:|
| `bad_deployment` | 1 | 6.00 | 40.0% | 2.00 | 4.00 |
| `db_connection_exhausted` | 1 | 5.00 | 50.0% | 2.00 | 3.00 |
| `db_replication_lag` | 3 | 5.00 | 50.0% | 1.67 | 2.67 |
| `db_slow_query` | 1 | 6.00 | 40.0% | 2.00 | 3.00 |
| `redis_memory_pressure` | 2 | 4.50 | 55.0% | 2.00 | 3.00 |
| `redis_low_hit_rate` | 2 | 5.00 | 50.0% | 2.00 | 3.00 |
| `kafka_consumer_lag` | 4 | 4.75 | 52.5% | 2.00 | 3.00 |
| `rpc_timeout` | 2 | 6.00 | 40.0% | 2.00 | 3.00 |
| `rpc_error_rate` | 2 | 5.00 | 50.0% | 2.00 | 3.00 |
| `resource_saturation` | 2 | 4.00 | 60.0% | 2.00 | 3.00 |
| `oom_restart` | 1 | 5.00 | 50.0% | 2.00 | 3.00 |
| `no_fault` | 4 | 4.75 | 52.5% | 2.00 | 3.00 |

结论：resource 类型的 Seed 已包含 metrics/logs，平均最省；bad deployment、DB slow query 和 RPC timeout 需要 6 个 Tool。没有 case 的 Tool/Expert 数超过 Fixed，但所有 Adaptive case 都增加了控制轮次。

## 6. 节省是否来自 Evidence Gate

不是主要来自 Evidence Gate 提前确认：

- dev 只有 `adaptive-db-dev-01`（1/25）满足 confidence、margin、两个独立 source_group 后停止。
- dev 其余 24/25 达到 Expert 预算上限 2 后停止。
- frozen-test 12/12 都因预算耗尽停止，没有 case 由充分证据 Gate 提前停止。

因此本实验能证明的是：**告警感知 Seed、领域裁剪和最多两个 Expert 的预算约束，在当前数据集上减少了调用且没有降低指标。** 它不能证明“大多数 case 因证据充分而提前停止”。

`Without Dynamic L2` 的调用更少也不能算优化：`max_expert_calls=0` 使 Gate 首轮就判定预算耗尽，dev Hit@1 降至 19.0%，test 降至 10.0%。这是“少查所以漏诊”。

## 7. 消融结论

| 对比 | 观察 | 能说明什么 |
|---|---|---|
| Adaptive Planner vs Full Adaptive | 两个 split 的质量和调用量完全相同 | 当前数据上 Domain Tool 的 raw Evidence 已足够；L2 Finding promotion 没有带来可测指标提升 |
| Without Dynamic L2 vs Adaptive Planner | dev Hit@1 `19.0%→100%`；test `10.0%→100%`，代价是约增加 2.2 个 Tool 和 2 个 Expert | 动态调用领域 Expert/Domain Tool 是识别 DB、Redis、Kafka、RPC 根因的关键 |
| Fixed vs Full Adaptive | 质量相同，Tool/Expert 约减半，但 rounds 和进程内 latency 增加 | 动态裁剪减少查询面，不等于当前实现降低端到端时延 |

## 8. 典型案例

### 8.1 少查工具仍正确：`platform-dev-02`

- 预期/实际：`resource_saturation`。
- Fixed：10 Tools、4 Experts；Full Adaptive：4 Tools、2 Experts、3 Rounds。
- Adaptive 只查 `metrics.query`, `logs.query`, `db.connections`, `redis.memory`，Tool 减少 60%。
- 但它最终因 Expert 预算耗尽停止，不应描述为 Evidence Gate 提前结束。

### 8.2 继续深挖后确认：`adaptive-db-dev-01`

- Seed：`metrics.query`, `traces.query`, `changes.query`。
- Trace 重建出 `order-service/payment-service/mysql` 异常路径；Round 1 只有一个独立来源，Gate 不充分。
- Round 2 调用 DB Expert，执行 `db.replication` 和 `db.slowlog`；得到 `replication_lag_seconds=15`。
- Trace 与 DB Tool 构成两个独立 source_group，Top1=`db_replication_lag(.98)`、margin=.18，Gate 充分后停止。
- 总计 5 Tools、1 Expert、2 Rounds；这是当前唯一直接展示 Evidence-Guided early stop 的 dev case。

### 8.3 失败/退化：无 Dynamic L2 的 `db-dev-01`

- Fixed、Adaptive Planner、Full Adaptive 都正确预测 `db_replication_lag`。
- Without Dynamic L2 只运行 Seed，不能调用 DB Expert 读取 snapshot 中的 DB 复制延迟，预测 `no_fault`。
- Full Adaptive 与 Fixed 在 dev/test 均无根因失败；该例是消融退化，不是 Full Adaptive 回归。

## 9. 每个 case 的预测

最后一列为 Full Adaptive 的 `Tools / Experts / Rounds`。Fixed 对每例均为 `10 / 4 / 1`。

### dev（25）

| Case | Expected | Fixed | Adaptive Planner | No Dynamic L2 | Full Adaptive | Full 开销 |
|---|---|---|---|---|---|---:|
| `db-dev-01` | `db_replication_lag` | `db_replication_lag` | `db_replication_lag` | `no_fault` | `db_replication_lag` | 5 / 2 / 3 |
| `db-dev-02` | `db_slow_query` | `db_slow_query` | `db_slow_query` | `no_fault` | `db_slow_query` | 6 / 2 / 3 |
| `db-dev-03` | `db_connection_exhausted` | `db_connection_exhausted` | `db_connection_exhausted` | `no_fault` | `db_connection_exhausted` | 5 / 2 / 3 |
| `db-dev-04` | `db_replication_lag` | `db_replication_lag` | `db_replication_lag` | `no_fault` | `db_replication_lag` | 5 / 2 / 3 |
| `redis-dev-01` | `redis_memory_pressure` | `redis_memory_pressure` | `redis_memory_pressure` | `no_fault` | `redis_memory_pressure` | 5 / 2 / 3 |
| `redis-dev-02` | `redis_low_hit_rate` | `redis_low_hit_rate` | `redis_low_hit_rate` | `no_fault` | `redis_low_hit_rate` | 5 / 2 / 3 |
| `redis-dev-03` | `redis_memory_pressure` | `redis_memory_pressure` | `redis_memory_pressure` | `no_fault` | `redis_memory_pressure` | 4 / 2 / 3 |
| `redis-dev-04` | `redis_low_hit_rate` | `redis_low_hit_rate` | `redis_low_hit_rate` | `no_fault` | `redis_low_hit_rate` | 5 / 2 / 3 |
| `kafka-dev-01` | `kafka_consumer_lag` | `kafka_consumer_lag` | `kafka_consumer_lag` | `no_fault` | `kafka_consumer_lag` | 4 / 2 / 3 |
| `kafka-dev-02` | `kafka_consumer_lag` | `kafka_consumer_lag` | `kafka_consumer_lag` | `no_fault` | `kafka_consumer_lag` | 5 / 2 / 3 |
| `kafka-dev-03` | `kafka_consumer_lag` | `kafka_consumer_lag` | `kafka_consumer_lag` | `no_fault` | `kafka_consumer_lag` | 5 / 2 / 3 |
| `kafka-dev-04` | `kafka_consumer_lag` | `kafka_consumer_lag` | `kafka_consumer_lag` | `no_fault` | `kafka_consumer_lag` | 5 / 2 / 3 |
| `rpc-dev-01` | `rpc_timeout` | `rpc_timeout` | `rpc_timeout` | `no_fault` | `rpc_timeout` | 6 / 2 / 3 |
| `rpc-dev-02` | `rpc_error_rate` | `rpc_error_rate` | `rpc_error_rate` | `no_fault` | `rpc_error_rate` | 5 / 2 / 3 |
| `rpc-dev-03` | `rpc_timeout` | `rpc_timeout` | `rpc_timeout` | `rpc_timeout` | `rpc_timeout` | 6 / 2 / 3 |
| `rpc-dev-04` | `rpc_error_rate` | `rpc_error_rate` | `rpc_error_rate` | `no_fault` | `rpc_error_rate` | 5 / 2 / 3 |
| `platform-dev-01` | `bad_deployment` | `bad_deployment` | `bad_deployment` | `no_fault` | `bad_deployment` | 6 / 2 / 4 |
| `platform-dev-02` | `resource_saturation` | `resource_saturation` | `resource_saturation` | `resource_saturation` | `resource_saturation` | 4 / 2 / 3 |
| `platform-dev-03` | `oom_restart` | `oom_restart` | `oom_restart` | `oom_restart` | `oom_restart` | 5 / 2 / 3 |
| `platform-dev-04` | `resource_saturation` | `resource_saturation` | `resource_saturation` | `resource_saturation` | `resource_saturation` | 4 / 2 / 3 |
| `normal-dev-01` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | 4 / 2 / 3 |
| `normal-dev-02` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | 5 / 2 / 3 |
| `normal-dev-03` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | 5 / 2 / 3 |
| `normal-dev-04` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | 5 / 2 / 3 |
| `adaptive-db-dev-01` | `db_replication_lag` | `db_replication_lag` | `db_replication_lag` | `db_connection_exhausted` | `db_replication_lag` | 5 / 1 / 2 |

### frozen-test（12）

| Case | Expected | Fixed | Adaptive Planner | No Dynamic L2 | Full Adaptive | Full 开销 |
|---|---|---|---|---|---|---:|
| `db-test-01` | `db_slow_query` | `db_slow_query` | `db_slow_query` | `no_fault` | `db_slow_query` | 5 / 2 / 3 |
| `db-test-02` | `db_connection_exhausted` | `db_connection_exhausted` | `db_connection_exhausted` | `no_fault` | `db_connection_exhausted` | 4 / 2 / 3 |
| `redis-test-01` | `redis_memory_pressure` | `redis_memory_pressure` | `redis_memory_pressure` | `no_fault` | `redis_memory_pressure` | 4 / 2 / 3 |
| `redis-test-02` | `redis_low_hit_rate` | `redis_low_hit_rate` | `redis_low_hit_rate` | `no_fault` | `redis_low_hit_rate` | 5 / 2 / 3 |
| `kafka-test-01` | `kafka_consumer_lag` | `kafka_consumer_lag` | `kafka_consumer_lag` | `no_fault` | `kafka_consumer_lag` | 4 / 2 / 3 |
| `kafka-test-02` | `kafka_consumer_lag` | `kafka_consumer_lag` | `kafka_consumer_lag` | `no_fault` | `kafka_consumer_lag` | 5 / 2 / 3 |
| `rpc-test-01` | `rpc_timeout` | `rpc_timeout` | `rpc_timeout` | `no_fault` | `rpc_timeout` | 6 / 2 / 3 |
| `rpc-test-02` | `rpc_error_rate` | `rpc_error_rate` | `rpc_error_rate` | `no_fault` | `rpc_error_rate` | 5 / 2 / 3 |
| `platform-test-01` | `bad_deployment` | `bad_deployment` | `bad_deployment` | `no_fault` | `bad_deployment` | 6 / 2 / 4 |
| `platform-test-02` | `oom_restart` | `oom_restart` | `oom_restart` | `oom_restart` | `oom_restart` | 5 / 2 / 3 |
| `normal-test-01` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | 4 / 2 / 3 |
| `normal-test-02` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | `no_fault` | 5 / 2 / 3 |

## 10. 严格结论与简历表述

### 可以得出的结论

在本仓库的 snapshot benchmark 上，Full Adaptive 相比 Fixed：

- dev 与 frozen-test 的 RCA 质量没有下降；
- Avg Tool Calls 减少约 50%～52%；
- Avg Expert Calls 减少约 50%～51%；
- 代价是调查轮数约从 1 增至 3，进程内耗时没有下降。

建议的简历表述：

> 设计告警感知的 Adaptive Investigation 与动态领域 Expert 调度；在 12-case frozen RCA test 上保持 Hit@1/Hit@3 100%、FPR 0 的同时，相比全量 Fixed Investigation 将平均 Tool 调用从 10.0 降至 4.83（-51.7%）、Expert 调用从 4.0 降至 2.0（-50.0%）。

### 不能宣传的内容

- 不能写“普遍通过 Evidence Gate 提前停止”：frozen-test 的 12/12 都由预算耗尽停止。
- 不能写“降低端到端时延”：当前进程内测量反而更高，且没有覆盖真实 HTTP、队列和 LLM。
- 不能把 12 个 frozen case 外推成生产环境收益。
- 不能声称 L2 Finding promotion 提升了准确率：当前 C/B 指标完全相同。

更准确的总判断是：**本实验支持“动态裁剪调查面、减少无关领域调用”，但目前节省主要由告警感知选择和预算上限保证，Evidence-Guided early stop 只在一个 dev case 中得到直接验证。**

## 11. 实验工件

| Split | Fixed | Adaptive Planner | No Dynamic L2 | Full Adaptive |
|---|---|---|---|---|
| dev | `artifacts/evaluations/20260911T114247Z-opspilot_fixed_planner-dev/` | `artifacts/evaluations/20260911T114248Z-opspilot_adaptive_planner-dev/` | `artifacts/evaluations/20260911T114249Z-opspilot_adaptive_without_dynamic_l2-dev/` | `artifacts/evaluations/20260911T114249Z-opspilot_full_adaptive-dev/` |
| frozen-test | `artifacts/evaluations/20260911T114250Z-opspilot_fixed_planner-test/` | `artifacts/evaluations/20260911T114251Z-opspilot_adaptive_planner-test/` | `artifacts/evaluations/20260911T114251Z-opspilot_adaptive_without_dynamic_l2-test/` | `artifacts/evaluations/20260911T114252Z-opspilot_full_adaptive-test/` |

每个目录均包含 `manifest.json`、`metrics.json`、`predictions.jsonl`、`failures.jsonl` 和系统生成的 `report.md`。
