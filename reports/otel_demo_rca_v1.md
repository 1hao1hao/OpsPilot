# OpenTelemetry 官方近真实环境 RCA v1 正式消融

OpsPilot 没有企业生产数据。所有表格读取本次真实实验 artifact；负结果、API 失败和 fallback 均保留。

运行 `20261005T041333Z-9b9d16`，状态 `completed`；保存的实际 RCA 63 次，其中完整恢复后纳入正式配对统计 63 次。失败/重试工件不删除，重复同一场景/模式/编号时采用最近完整 trial。工件 `artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16`。

## Environment / 冻结配置

Upstream `7ea09b865a9b9d411b59459b15133d84f8c2784a`；Demo 3.1.0，Collector 0.160.0。OpsPilot HEAD `fcc74f5c857d7a52f702a542f43b4ce3bb646c06` 加不可变 source_manifest/source archive；未提交源码纳入快照，没有覆盖用户工作区。
Dataset `otel-demo-rca@1.0.1`；experiment `otel-demo-rca-release@1.0.1`；固定官方 Locust 5 用户、spawn=1。release 所有时间参数保存在 config/dataset/各 trial 的 scenario.json。
真实 Planner 模型 `deepseek-v4-flash`，temperature=0，max_tokens=500，attempts=1；原始 response usage 已采集，未配置可靠单价，Estimated Cost=N/A。

## Fault Dataset / Modes

6 faults + Normal，Ground Truth 仅 Controller/scorer；Engine 只收到随机 ID symptom Alert 与脱敏真实工具数据。changes.query 不读取 flags。
Full Adaptive：现有 Coordinator/L1/Gate + 真实 LLM Planner，记录未触发 Planner 的 Gate 提前结束和 fallback；Fixed Full：串行实际执行全部 13 工具、同一 Evidence/Ranker/Gate，无 LLM、无 Expert 调用；No-L2：同一真实 Planner，Expert budget=0。所有模式使用相同确定性排序和摘要，不使用 LLM 改写候选。
配对设计：每次注入/恢复周期，三个模式分别对同一个固定 Alert 时间窗发起真实 backend queries，顺序按重复号轮换。21 生命周期对应 63 次真实 RCA；不是把一个结果复制三份。工具请求次数、底层 retries、API calls 与语义 Tool calls 分开保存。Controller 为测量完整遥测另做的全工具采集不计入任何模式的 RCA 调用成本。

## Overall results

| Mode | Scored | Fault Top1 | Fault Top3 | Normal | Gate | Budget | Degraded | Avg Tool | Avg Expert | Avg rounds | Avg LLM | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_adaptive | 21 | 10/18 | 12/18 | 2/3 | 0/21 | 21/21 | 0/21 | 6.10 | 1.00 | 4.00 | 3.00 | 17038.33 | 21673.26 |
| fixed_full | 21 | 10/18 | 11/18 | 0/3 | 0/21 | 0/21 | 0/21 | 13.00 | 0.00 | 1.00 | 0.00 | 27408.37 | 28744.40 |
| adaptive_no_l2 | 21 | 10/18 | 12/18 | 2/3 | 0/21 | 21/21 | 0/21 | 5.71 | 0.00 | 4.00 | 3.00 | 13809.91 | 14738.43 |

Fixed 的 Budget=0 表示没有自适应预算中止；执行 13 工具超出 Adaptive 的 8 工具预算是本 baseline 定义，不能声称相同成本约束。Gate pass 与 Top1 正确是独立指标。

## Per-scenario results

| Scenario | Mode | Fault observed | Top1 | Top3 | Normal | Gate | Budget | Degraded | Tool/Expert/Rounds/LLM avg | P50/P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | full_adaptive | 0 | N/A | N/A | 2/3 | 0/3 | 3/3 | 0/3 | 6.67/0.67/4.00/3.00 | 13719.34/20877.87 |
| normal | fixed_full | 0 | N/A | N/A | 0/3 | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 26775.08/28360.88 |
| normal | adaptive_no_l2 | 0 | N/A | N/A | 2/3 | 0/3 | 3/3 | 0/3 | 6.00/0.00/4.00/3.00 | 13198.51/14175.35 |
| compute | full_adaptive | 3 | 3/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 5.00/0.67/4.00/3.00 | 15269.31/16385.90 |
| compute | fixed_full | 3 | 3/3 | 3/3 | N/A | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 24979.23/26391.90 |
| compute | adaptive_no_l2 | 3 | 3/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 5.00/0.00/4.00/3.00 | 12873.17/13088.33 |
| payment-errors | full_adaptive | 3 | 3/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 6.67/1.33/4.00/3.00 | 17566.71/20728.35 |
| payment-errors | fixed_full | 3 | 3/3 | 3/3 | N/A | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 25952.90/26420.54 |
| payment-errors | adaptive_no_l2 | 3 | 3/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 6.00/0.00/4.00/3.00 | 13654.54/14081.31 |
| dependency-unavailable | full_adaptive | 3 | 3/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 6.00/1.00/4.00/3.00 | 16749.21/17018.43 |
| dependency-unavailable | fixed_full | 3 | 3/3 | 3/3 | N/A | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 28744.40/28762.46 |
| dependency-unavailable | adaptive_no_l2 | 3 | 3/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 6.00/0.00/4.00/3.00 | 14738.43/15618.99 |
| messaging-backlog | full_adaptive | 3 | 0/3 | 0/3 | N/A | 0/3 | 3/3 | 0/3 | 7.33/1.33/4.00/3.00 | 20874.44/22001.19 |
| messaging-backlog | fixed_full | 3 | 0/3 | 0/3 | N/A | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 27408.37/28331.99 |
| messaging-backlog | adaptive_no_l2 | 3 | 0/3 | 0/3 | N/A | 0/3 | 3/3 | 0/3 | 6.00/0.00/4.00/3.00 | 14591.28/14711.36 |
| database-latency | full_adaptive | 3 | 0/3 | 0/3 | N/A | 0/3 | 3/3 | 0/3 | 6.00/1.00/4.00/3.00 | 17317.06/17744.58 |
| database-latency | fixed_full | 3 | 0/3 | 0/3 | N/A | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 28026.15/28140.38 |
| database-latency | adaptive_no_l2 | 3 | 0/3 | 0/3 | N/A | 0/3 | 3/3 | 0/3 | 6.00/0.00/4.00/3.00 | 14357.75/14695.94 |
| memory-growth | full_adaptive | 3 | 1/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 5.00/1.00/4.00/3.00 | 17186.63/17220.68 |
| memory-growth | fixed_full | 3 | 1/3 | 2/3 | N/A | 0/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 28311.82/28363.44 |
| memory-growth | adaptive_no_l2 | 3 | 1/3 | 3/3 | N/A | 0/3 | 3/3 | 0/3 | 5.00/0.00/4.00/3.00 | 13809.91/13934.92 |

## Adaptive vs Fixed / L2 Expert ablation

- full_adaptive 相对 Fixed：Fault Top1 差值 0.00 个百分点；Tool calls 减少 53.11%；P50/P95 latency 差值 -10370.03/-7071.14ms。
- full_adaptive 真正使用 Planner 的运行 21/21，fallback 运行 0；API 成功/失败=63/0，有效决策=63，fallback 决策=0；已记录 usage 63/63，tokens={'prompt_tokens': 90596, 'completion_tokens': 3370, 'total_tokens': 93966}。
- adaptive_no_l2 相对 Fixed：Fault Top1 差值 0.00 个百分点；Tool calls 减少 56.04%；P50/P95 latency 差值 -13598.46/-14005.96ms。
- adaptive_no_l2 真正使用 Planner 的运行 21/21，fallback 运行 0；API 成功/失败=63/0，有效决策=63，fallback 决策=0；已记录 usage 63/63，tokens={'prompt_tokens': 89277, 'completion_tokens': 3228, 'total_tokens': 92505}。

本次 Fault Top1 最高：['full_adaptive', 'fixed_full', 'adaptive_no_l2']；平均 Tool calls 最低：['adaptive_no_l2']；P50 latency 最低：adaptive_no_l2。Fixed LLM calls=0；这些成本维度不能未经单价配置合成现金成本。
L2 消融：Full 相对 No-L2 的 Fault Top1 差值=0.00 个百分点；平均 Expert calls=1.00/0.00。按场景表核对收益，三次重复不证明显著性。

各场景有价值/更稳定的判断以逐场景 Top1/Top3、调用数和延迟为依据。Expert 是按需实际调用，不能将 Fixed 的直接域工具查询算作 Expert 调用。三个重复仅为初步配对结果，没有统计显著性结论；LLM API 数量降低不等于现金成本降低。

## Failure analysis

错误 Top1 29 次；主要观测条件计数 `{'G': 8, 'unknown': 3, 'C': 3, 'F': 6, 'D': 9}`；多标签计数 `{'G': 18, 'unknown': 3, 'C': 3, 'I': 15, 'F': 6, 'D': 9}`。完整证据与对应文件见 failure_analysis.json/md。自动分类不调用 LLM，条件共现不证明唯一因果，无法确定的因果标为 unknown。

## Limitations

类别评分，不宣称精确服务定位。paymentUnreachable 的连接/DNS ERROR 不等于 timeout；DB_SLOW_QUERY 不等于锁竞争机制；内存增长不等于 OOM，email 人工恢复重启另记。Controller 的无故障 baseline 与 Engine 包含稳定阶段的历史窗口不同；保持前版 release 窗口冻结，后续单独消融。
部分 deployment/topology/DB/cache 指标缺失；trace/log 窗口限额和稀疏流量影响采样。官方负载还包含未部署可选 agent 的 /prompt 背景请求失败。Normal 仅表示固定负载下的无故障注入状态。三个模式共享同一生命周期但实际发起查询，查询顺序轮换仍不能排除异步采样差异。

## Knowledge Tool decision

INCONCLUSIVE

尚无经核验的历史 incident/runbook 证明增量区分信息；本轮还存在工具选择、排名、Gate、预算、采样/窗口或 taxonomy 限制，不能从错例直接推断需要 RAG。

当前 Evidence/错例逐项见 failure_analysis.json；外部知识候选仅为待核验设想，未证明实时 Evidence 无法区分，也没有把 feature flags 或 benchmark 答案当作知识库。
先修复并冻结 telemetry/window/tool selection/ranking 问题，再核验独立脱敏知识文档；若五项标准满足，单独做 Without/With Knowledge，比较 Top1/Top3、Tool Cost、Latency，知识不能直接决定 RootCause。

本任务没有开发/接入 EvalRAG 或 Knowledge Tool。

## 完整性复核与逐次真实 LLM 使用

客户端时区 Asia/Shanghai：2026-10-05T15:34:35.878601+08:00；真实完整 RCA=63，完整生命周期=21，确认产生故障效果的生命周期=18。逐场景/模式 repeats 在 release_completeness.json 中验证，未用支持 repeat=3 冒充实际重复。

### Controller Ground Truth mapping

| Scenario | 实际 flag / variant | operational category | root service | mechanism / gap |
| --- | --- | --- | --- | --- |
| normal | all off | no_fault | frontend | all faults disabled under the same continuous load;  |
| compute | adHighCpu/on | resource_saturation | ad | CPU workload introduced in advertising service;  |
| payment-errors | paymentFailure/100% | rpc_error_rate | payment | rejected payment requests at a fixed probability of one;  |
| dependency-unavailable | paymentUnreachable/on | rpc_error_rate | payment | checkout directs payment RPC to an unreachable endpoint; Real smoke showed PaymentService/Charge ERROR with is_timeout=false; unreachable endpoint produces connection or DNS/UNAVAILABLE errors. Existing RPC_ERROR_RATE expresses the error symptom, not a dedicated downstream-unavailable mechanism. No new type or diagnosis rule added. |
| messaging-backlog | kafkaQueueProblems/on | kafka_consumer_lag | kafka | checkout amplifies Kafka message production and consumer delay;  |
| database-latency | productCatalogLockContention/on | db_slow_query | astronomy-db | periodic ACCESS EXCLUSIVE lock on catalog.products; Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these. |
| memory-growth | emailMemoryLeak/100x | resource_saturation | email | larger retained email deliveries create a rising memory footprint; Memory growth is a resource symptom; do not claim OOM unless independently observed. |

### 哪些场景 Adaptive 有收益 / Fixed 更稳定

下表只描述本轮实际配对差值，不声称三个重复具备统计显著性或唯一因果。

| Scenario | Full / Fixed / No-L2 Top1（Normal 为 NO_FAULT） | Full 相对 Fixed 工具降幅 | Full / Fixed P50 ms | Full / No-L2 Avg Expert |
| --- | --- | --- | --- | --- |
| normal | 2/3 / 0/3 / 2/3 | 48.72% | 13719.34/26775.08 | 0.67/0.00 |
| compute | 3/3 / 3/3 / 3/3 | 61.54% | 15269.31/24979.23 | 0.67/0.00 |
| payment-errors | 3/3 / 3/3 / 3/3 | 48.72% | 17566.71/25952.90 | 1.33/0.00 |
| dependency-unavailable | 3/3 / 3/3 / 3/3 | 53.85% | 16749.21/28744.40 | 1.00/0.00 |
| messaging-backlog | 0/3 / 0/3 / 0/3 | 43.59% | 20874.44/27408.37 | 1.33/0.00 |
| database-latency | 0/3 / 0/3 / 0/3 | 53.85% | 17317.06/28026.15 | 1.00/0.00 |
| memory-growth | 1/3 / 1/3 / 1/3 | 61.54% | 17186.63/28311.82 | 1.00/0.00 |

所有模式都用串行工具执行来控制执行策略；本轮没有评估 Fixed 全量并行查询，因此不代表最优 Fixed 延迟。No-L2 未采集域工具可能是预算=0 的预期限制，failure label F 仅表示遗漏区分性工具，不能单凭它断言 Planner 做了非法决策。

### 真正 LLM / fallback / Planner 未触发的子集

模式名称表示配置的策略；逐次是否真正用了 LLM 如下。含 fallback 的运行不会描述为纯 LLM 成功；Gate 在 L1 结束时也不会伪造一次模型调用。

| Mode | 子集 | runs | Fault Top1 | Fault Top3 | Normal | Avg Tool | P50/P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_adaptive | real_llm_without_fallback | 21 | 10/18 | 12/18 | 2/3 | 6.10 | 17038.33/21673.26 |
| full_adaptive | with_fallback | 0 | N/A | N/A | N/A | N/A | N/A |
| full_adaptive | planner_not_triggered | 0 | N/A | N/A | N/A | N/A | N/A |
| adaptive_no_l2 | real_llm_without_fallback | 21 | 10/18 | 12/18 | 2/3 | 5.71 | 13809.91/14738.43 |
| adaptive_no_l2 | with_fallback | 0 | N/A | N/A | N/A | N/A | N/A |
| adaptive_no_l2 | planner_not_triggered | 0 | N/A | N/A | N/A | N/A | N/A |

### 每次调用与候选

| Scenario/repeat | Mode | Top1 correct | Predicted | Tool/Expert | LLM used | fallback | API ok/fail | tokens（已返回 usage） | latency ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal/1 | full_adaptive | False | rpc_timeout | 8/2 | True | False | 3/0 | 2271 | 21673.26 |
| normal/1 | fixed_full | False | redis_low_hit_rate, redis_memory_pressure, rpc_timeout | 13/0 | False | False | 0/0 | 0 | 26250.87 |
| normal/1 | adaptive_no_l2 | False | rpc_timeout | 6/0 | True | False | 3/0 | 1567 | 12975.56 |
| normal/2 | fixed_full | False | redis_low_hit_rate, redis_memory_pressure | 13/0 | False | False | 0/0 | 0 | 28537.08 |
| normal/2 | adaptive_no_l2 | True | no_fault | 6/0 | True | False | 3/0 | 1233 | 14283.89 |
| normal/2 | full_adaptive | True | no_fault | 6/0 | True | False | 3/0 | 1254 | 13719.34 |
| normal/3 | adaptive_no_l2 | True | no_fault | 6/0 | True | False | 3/0 | 1215 | 13198.51 |
| normal/3 | full_adaptive | True | no_fault | 6/0 | True | False | 3/0 | 1275 | 13427.50 |
| normal/3 | fixed_full | False | redis_low_hit_rate, redis_memory_pressure | 13/0 | False | False | 0/0 | 0 | 26775.08 |
| compute/1 | full_adaptive | True | resource_saturation, rpc_timeout | 5/0 | True | False | 3/0 | 3038 | 12177.73 |
| compute/1 | fixed_full | True | resource_saturation, rpc_timeout, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 24462.40 |
| compute/1 | adaptive_no_l2 | True | resource_saturation, rpc_timeout | 5/0 | True | False | 3/0 | 2957 | 12452.40 |
| compute/2 | fixed_full | True | resource_saturation, rpc_timeout, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 26548.86 |
| compute/2 | adaptive_no_l2 | True | resource_saturation, rpc_timeout | 5/0 | True | False | 3/0 | 2356 | 13112.24 |
| compute/2 | full_adaptive | True | resource_saturation, rpc_timeout | 5/1 | True | False | 3/0 | 2463 | 16509.96 |
| compute/3 | adaptive_no_l2 | True | resource_saturation, rpc_timeout | 5/0 | True | False | 3/0 | 2662 | 12873.17 |
| compute/3 | full_adaptive | True | resource_saturation, rpc_timeout | 5/1 | True | False | 3/0 | 2719 | 15269.31 |
| compute/3 | fixed_full | True | resource_saturation, rpc_timeout, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 24979.23 |
| payment-errors/1 | full_adaptive | True | rpc_error_rate, rpc_timeout | 8/2 | True | False | 3/0 | 7969 | 21079.64 |
| payment-errors/1 | fixed_full | True | rpc_error_rate, rpc_timeout, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 26472.50 |
| payment-errors/1 | adaptive_no_l2 | True | rpc_error_rate, rpc_timeout | 6/0 | True | False | 3/0 | 7516 | 14128.73 |
| payment-errors/2 | fixed_full | True | rpc_error_rate, resource_saturation, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 14039.89 |
| payment-errors/2 | adaptive_no_l2 | True | rpc_error_rate, resource_saturation | 6/0 | True | False | 3/0 | 7443 | 8231.73 |
| payment-errors/2 | full_adaptive | True | rpc_error_rate, resource_saturation | 6/1 | True | False | 3/0 | 7736 | 10189.63 |
| payment-errors/3 | adaptive_no_l2 | True | rpc_error_rate, resource_saturation, rpc_timeout | 6/0 | True | False | 3/0 | 7589 | 13654.54 |
| payment-errors/3 | full_adaptive | True | rpc_error_rate, resource_saturation, rpc_timeout | 6/1 | True | False | 3/0 | 7710 | 17566.71 |
| payment-errors/3 | fixed_full | True | rpc_error_rate, resource_saturation, rpc_timeout | 13/0 | False | False | 0/0 | 0 | 25952.90 |
| dependency-unavailable/1 | full_adaptive | True | rpc_error_rate, resource_saturation | 6/1 | True | False | 3/0 | 5925 | 12577.88 |
| dependency-unavailable/1 | fixed_full | True | rpc_error_rate, resource_saturation, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 28764.46 |
| dependency-unavailable/1 | adaptive_no_l2 | True | rpc_error_rate, resource_saturation | 6/0 | True | False | 3/0 | 5848 | 15716.83 |
| dependency-unavailable/2 | fixed_full | True | rpc_error_rate, rpc_timeout, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 28744.40 |
| dependency-unavailable/2 | adaptive_no_l2 | True | rpc_error_rate, rpc_timeout, resource_saturation | 6/0 | True | False | 3/0 | 6263 | 13954.22 |
| dependency-unavailable/2 | full_adaptive | True | rpc_error_rate, rpc_timeout, resource_saturation | 6/1 | True | False | 3/0 | 5046 | 16749.21 |
| dependency-unavailable/3 | adaptive_no_l2 | True | rpc_error_rate, rpc_timeout, resource_saturation | 6/0 | True | False | 3/0 | 6117 | 14738.43 |
| dependency-unavailable/3 | full_adaptive | True | rpc_error_rate, rpc_timeout, resource_saturation | 6/1 | True | False | 3/0 | 4988 | 17048.35 |
| dependency-unavailable/3 | fixed_full | True | rpc_error_rate, rpc_timeout, resource_saturation | 13/0 | False | False | 0/0 | 0 | 26587.32 |
| messaging-backlog/1 | full_adaptive | False | rpc_timeout | 8/2 | True | False | 3/0 | 2946 | 20874.44 |
| messaging-backlog/1 | fixed_full | False | rpc_timeout, redis_low_hit_rate, redis_memory_pressure | 13/0 | False | False | 0/0 | 0 | 26434.20 |
| messaging-backlog/1 | adaptive_no_l2 | False | rpc_timeout | 6/0 | True | False | 3/0 | 2476 | 14591.28 |
| messaging-backlog/2 | fixed_full | False | redis_low_hit_rate, redis_memory_pressure | 13/0 | False | False | 0/0 | 0 | 27408.37 |
| messaging-backlog/2 | adaptive_no_l2 | False | no_fault | 6/0 | True | False | 3/0 | 1237 | 13656.79 |
| messaging-backlog/2 | full_adaptive | False | no_fault | 6/0 | True | False | 3/0 | 1273 | 13514.08 |
| messaging-backlog/3 | adaptive_no_l2 | False | rpc_timeout, resource_saturation | 6/0 | True | False | 3/0 | 2815 | 14724.70 |
| messaging-backlog/3 | full_adaptive | False | rpc_timeout, resource_saturation | 8/2 | True | False | 3/0 | 3215 | 22126.38 |
| messaging-backlog/3 | fixed_full | False | rpc_timeout, redis_low_hit_rate, redis_memory_pressure | 13/0 | False | False | 0/0 | 0 | 28434.62 |
| database-latency/1 | full_adaptive | False | rpc_timeout, rpc_error_rate, resource_saturation | 6/1 | True | False | 3/0 | 7742 | 17792.08 |
| database-latency/1 | fixed_full | False | rpc_timeout, rpc_error_rate, resource_saturation | 13/0 | False | False | 0/0 | 0 | 28153.08 |
| database-latency/1 | adaptive_no_l2 | False | rpc_timeout, rpc_error_rate, resource_saturation | 6/0 | True | False | 3/0 | 7695 | 14733.52 |
| database-latency/2 | fixed_full | False | rpc_timeout, rpc_error_rate, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 28026.15 |
| database-latency/2 | adaptive_no_l2 | False | rpc_timeout, rpc_error_rate | 6/0 | True | False | 3/0 | 7615 | 13803.23 |
| database-latency/2 | full_adaptive | False | rpc_timeout, rpc_error_rate | 6/1 | True | False | 3/0 | 8014 | 16975.39 |
| database-latency/3 | adaptive_no_l2 | False | rpc_timeout, rpc_error_rate, resource_saturation | 6/0 | True | False | 3/0 | 7775 | 14357.75 |
| database-latency/3 | full_adaptive | False | rpc_timeout, rpc_error_rate, resource_saturation | 6/1 | True | False | 3/0 | 8109 | 17317.06 |
| database-latency/3 | fixed_full | False | rpc_timeout, rpc_error_rate, resource_saturation | 13/0 | False | False | 0/0 | 0 | 27889.90 |
| memory-growth/1 | full_adaptive | False | rpc_timeout, resource_saturation | 5/1 | True | False | 3/0 | 2228 | 17224.46 |
| memory-growth/1 | fixed_full | False | rpc_timeout, redis_low_hit_rate, redis_memory_pressure | 13/0 | False | False | 0/0 | 0 | 28311.82 |
| memory-growth/1 | adaptive_no_l2 | False | rpc_timeout, resource_saturation | 5/0 | True | False | 3/0 | 2246 | 13574.57 |
| memory-growth/2 | fixed_full | True | resource_saturation, rpc_timeout, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 28369.18 |
| memory-growth/2 | adaptive_no_l2 | True | resource_saturation, rpc_timeout | 5/0 | True | False | 3/0 | 2743 | 13948.81 |
| memory-growth/2 | full_adaptive | True | resource_saturation, rpc_timeout | 5/1 | True | False | 3/0 | 2932 | 17186.63 |
| memory-growth/3 | adaptive_no_l2 | False | rpc_timeout, resource_saturation | 5/0 | True | False | 3/0 | 5137 | 13809.91 |
| memory-growth/3 | full_adaptive | False | rpc_timeout, resource_saturation | 5/1 | True | False | 3/0 | 5113 | 17038.33 |
| memory-growth/3 | fixed_full | False | rpc_timeout, resource_saturation, redis_low_hit_rate | 13/0 | False | False | 0/0 | 0 | 28051.93 |

Token 合计只包括已返回 usage 的响应；失败时未返回 usage 不代表零消费。现金成本未估计。逐次 raw response 和请求在各模式 llm/，未保存授权头或 API key。

### 当前实时 Evidence 的限制与 Knowledge 决策依据

Normal 的全部故障 off 不表示所有后台请求无错误。现有 traces.query 把超过阈值的慢 span 支持到 RPC_TIMEOUT，需要结合 RPC 协议状态和业务 scope 核查；长 span 不能单独证明协议 timeout。Fixed 的全局 Redis hit-rate 标量没有域基线变化上下文，固定阈值可能把稳定的正常低命中率当故障。控制器另存 baseline，不能据此声称 Engine 已看到同样基线。
这些是待核验的实时采集/证据/排名/工具选择问题；本轮没有经验证的独立历史 incident/runbook corpus，不能跳过基础问题直接建议 EvalRAG。Knowledge Tool 决策与错例路径见 artifacts/knowledge_tool_decision.md 和 failure_analysis.json。

### 版本与失败现场

1.0.0 在 CPU gauge 恢复检查中中止，单独保留于 artifacts/otel_demo_rca_v1/20261005T032959Z-d57607/ 和 reports/otel_demo_rca_v1_interrupted.md。1.0.1 全量重新执行；只在新冻结前修复通用 JSON 请求声明、CPU counter rate 与匿名控制日志隔离，未逐 Case 改 Planner/Gate/Ranker、负载、window、variant 或答案。

## 最终独立验证

实际 repeat matrix、源码/配置不变、Agent boundary 隔离、API key 未落盘及调用计数通过 independent_audit.json；全部故障 off，28 服务运行，官方 clone clean。最终 pytest 273 passed / 4 skipped，Ruff 通过；详见 `artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16/final_verification.json` 与 pytest_final.log / ruff_final.log。

## 已保存输入与冻结规则的错误条件复核

Fixed 的三次 Kafka lag 实测为 707、808、606，冻结 Evidence converter 要求 lag≥1000，因此没有生成 Kafka 支持 Evidence。Fixed 的 DB slowlog 计数 27、22、16 均生成 DB_SLOW_QUERY 支持 Evidence，但该类别未进入 Top3；Full/No-L2 均未采集 db.slowlog。自动补充 C/D/F 条件，仍将唯一因果记为 unknown，No-L2 工具遗漏可由 Expert budget=0 的配置约束解释。未改任何门槛、Planner、Ranker 或实验得分；原分析保存在本地 analysis_refinement/，复核摘要 failure_condition_refinement.json。
