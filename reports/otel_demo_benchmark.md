# OpenTelemetry 官方近真实微服务环境 RCA Benchmark

OpsPilot 没有企业生产数据。本实验使用官方 Astronomy Shop、真实 Locust、feature-flag 故障注入和真实 telemetry；未运行真实 LLM 大规模实验。
前置 task1.5 已完成，LIVE_INTEGRATION=PASS，见 `reports/otel_demo_live_smoke.md`。本报告所有分数和观测来自以下保存的工件；不会将流程成功写成诊断正确。

## release：20261004T151904Z-91254d

Dataset `otel-demo-rca@1.0.1`，SHA-256 `05512e207db1a90268ca04bde6ceab19c6729d07279847a5f8e9c9dd1236c21a`；实际 repeat override=1，Normal 按 profile 真实重复。
Upstream `7ea09b865a9b9d411b59459b15133d84f8c2784a`；Demo 3.1.0；Collector 0.160.0。负载固定 5 用户、spawn rate 1。

| Scenario | 已执行/完整生命周期 | 故障效果确认 | Fault Top1 | Fault Top3 | Normal NO_FAULT | Gate pass | Budget exhausted | Degraded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 3/3 | 0 | N/A | N/A | 1/3 | 1/3 | 2/3 | 0/3 |
| compute | 1/1 | 1 | 1/1 | 1/1 | N/A | 0/1 | 1/1 | 0/1 |
| payment-errors | 1/1 | 1 | 1/1 | 1/1 | N/A | 0/1 | 1/1 | 0/1 |
| dependency-unavailable | 1/1 | 1 | 1/1 | 1/1 | N/A | 0/1 | 1/1 | 0/1 |
| messaging-backlog | 1/1 | 1 | 0/1 | 0/1 | N/A | 0/1 | 1/1 | 0/1 |
| database-latency | 1/1 | 1 | 0/1 | 0/1 | N/A | 0/1 | 1/1 | 0/1 |
| memory-growth | 1/1 | 1 | 0/1 | 1/1 | N/A | 1/1 | 0/1 | 0/1 |

Fault 总计：Top1 **3/6**，Top3 **4/6**；Normal **1/3**。
总体：完整生命周期 9/9，已评分 9；Gate pass 2/9，Budget exhausted 7/9，Degraded 0/9。
已确认产生可观测差异的 Fault 子集：Top1 3/6，Top3 4/6。未确认故障差异的分数仍在原始 score 中保存，但不能代表有效故障诊断准确率。

### 实际 trial、遥测与失败

| Case/repeat | 状态 | 实际预测 Top-K | tool/expert/rounds | RCA latency ms | 恢复 health/queries/symptom |
| --- | --- | --- | --- | --- | --- |
| normal/1 | completed | no_fault | 6/0/4 | 8955.616 | True/True/None |
| normal/2 | completed | rpc_timeout | 4/1/2 | 4689.156 | True/True/None |
| normal/3 | completed | rpc_timeout | 6/1/4 | 5575.872 | True/True/None |
| compute/1 | completed | resource_saturation, rpc_timeout | 5/1/4 | 10019.563 | True/True/True |
| payment-errors/1 | completed | rpc_error_rate, rpc_timeout, resource_saturation | 6/1/4 | 12838.255000000001 | True/True/True |
| dependency-unavailable/1 | completed | rpc_error_rate, rpc_timeout, resource_saturation | 6/1/4 | 12343.780999999999 | True/True/True |
| messaging-backlog/1 | completed | rpc_timeout, resource_saturation | 6/1/4 | 12664.529 | True/True/True |
| database-latency/1 | completed | rpc_timeout, rpc_error_rate, resource_saturation | 6/1/4 | 12807.179 | True/True/True |
| memory-growth/1 | completed | rpc_timeout, resource_saturation | 4/1/3 | 11982.821 | True/True/True |

#### normal #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/normal/repeat-01`。总实验时长 332.1 秒。
故障效果确认：None（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=10.383679455981866 → incident=12.550418347278244。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=180.19230769230737 → incident=154.37499999999892。
- `metrics.query.tp99`：baseline=988.5000000000013 → incident=1123.4999999999982。
- `metrics.query.cpu_usage`：baseline=0.021729052440699094 → incident=0.02178401089454064。
- `metrics.query.memory_usage`：baseline=0.7086875 → incident=0.71053125。
- `redis.hotkeys.hit_rate_percent`：baseline=35.77235772357723 → incident=36.29032258064516。
- `redis.memory.used_memory`：baseline=1416344.0 → incident=1417176.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=None → incident=6.8012032。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=47.89825641025641。
- `rpc.metrics.call_volume`：baseline=None → incident=40。
- `cpu_usage` 趋势：{"samples": 4, "first": 0.021768842135071968, "last": 0.02178401089454064, "slope_per_second": 3.3436255235335295e-07}。
- `memory_usage` 趋势：{"samples": 4, "first": 0.706859375, "last": 0.71053125, "slope_per_second": 4.156249999999936e-05}。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 8}
Engine 实际选择工具：metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query；Controller 的全工具采集不计入 RCA tool_calls。

#### normal #2

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/normal/repeat-02`。总实验时长 300.3 秒。
故障效果确认：None（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=10.933880027334698 → incident=10.967215027418037。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=48.60000000000005 → incident=46.59259259259256。
- `metrics.query.tp99`：baseline=762.6666666666667 → incident=1506.499999999999。
- `metrics.query.cpu_usage`：baseline=0.021927437860214477 → incident=0.021977627665942818。
- `metrics.query.memory_usage`：baseline=0.7114218749999999 → incident=0.711453125。
- `redis.hotkeys.hit_rate_percent`：baseline=38.75968992248062 → incident=29.090909090909093。
- `redis.memory.used_memory`：baseline=1418680.0 → incident=1417784.0。
- `rpc.metrics.timeout_rate`：baseline=None → incident=0.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=None → incident=34.994747914893615。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=7.948188444444445。
- `rpc.metrics.call_volume`：baseline=None → incident=47。
- `cpu_usage` 趋势：{"samples": 4, "first": 0.0219589323310566, "last": 0.021977627665942818, "slope_per_second": 4.149052020352197e-07}。
- `memory_usage` 趋势：{"samples": 4, "first": 0.708296875, "last": 0.711453125, "slope_per_second": 5.291666666666621e-05}。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 12}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `rpc.timeout` (rpc.metrics)：RPC timeout rate=0.0%, latency ratio=4.4x。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=1506.499999999999, historical baselines={'last_week': None, 'yesterday': 947.1666666666657}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=46.59259259259256, historical baselines={'last_week': None, 'yesterday': 129.89159663865516}。

#### normal #3

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/normal/repeat-03`。总实验时长 298.7 秒。
故障效果确认：None（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=2.0 → incident=0.0。
- `metrics.query.qps`：baseline=10.583686122870763 → incident=12.200610030501524。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=202.77777777777777 → incident=144.9999999999989。
- `metrics.query.tp99`：baseline=782.499999999999 → incident=1135.999999999998。
- `metrics.query.cpu_usage`：baseline=0.02210114231942297 → incident=0.022163084005736158。
- `metrics.query.memory_usage`：baseline=0.70671875 → incident=0.71159375。
- `redis.hotkeys.hit_rate_percent`：baseline=37.68115942028986 → incident=32.55813953488372。
- `redis.memory.used_memory`：baseline=1413848.0 → incident=1414488.0。
- `rpc.metrics.timeout_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.error_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=5.341940869565217 → incident=4.342501517241379。
- `rpc.metrics.baseline_latency_ms`：baseline=28.61736838095238 → incident=9.135707022222222。
- `rpc.metrics.call_volume`：baseline=23 → incident=29。
- `cpu_usage` 趋势：{"samples": 4, "first": 0.02210418717359801, "last": 0.022163084005736158, "slope_per_second": 1.254781499920495e-06}。
- `memory_usage` 趋势：{"samples": 4, "first": 0.706171875, "last": 0.71159375, "slope_per_second": 8.84375000000004e-05}。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 8}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics, traces.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=144.9999999999989, historical baselines={'last_week': None, 'yesterday': 95.51587301587303}。

#### compute #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/compute/repeat-01`。总实验时长 557.4 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.13333777792593088 → incident=0.15000750037501875。
- `metrics.query.tp95`：baseline=3.1999999999999993 → incident=1.9。
- `metrics.query.tp99`：baseline=3.8399999999999985 → incident=1.98。
- `metrics.query.cpu_usage`：baseline=0.0001478546293284443 → incident=0.13985553678569349。
- `redis.hotkeys.hit_rate_percent`：baseline=35.294117647058826 → incident=34.61538461538461。
- `redis.memory.used_memory`：baseline=1413912.0 → incident=1412120.0。
- `cpu_usage` 趋势：{"samples": 16, "first": 0.0001478546293284443, "last": 0.13985553678569349, "slope_per_second": 0.0006758532544464267}。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 12}
Engine 实际选择工具：metrics.query, logs.query, rpc.metrics, traces.query, changes.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=1.9, historical baselines={'last_week': None, 'yesterday': 7.570000000000002}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=1.98, historical baselines={'last_week': None, 'yesterday': 11.913999999999998}。
- Evidence `anomaly.iqr` (metrics.query)：cpu_usage: IQR level_shift, current=0.13985553678569349, median=0.023501638193475456, bounds=[-0.20980823679912913, 0.34994802164044003]。
- Evidence `anomaly.historical_baseline` (metrics.query)：cpu_usage: current=0.13985553678569349, historical baselines={'last_week': None, 'yesterday': 0.05147072335721999}。

#### payment-errors #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/payment-errors/repeat-01`。总实验时长 522.3 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.01666722224074136 → incident=0.06667111140742717。
- `metrics.query.error_rate`：baseline=0.0 → incident=100.0。
- `metrics.query.tp95`：baseline=97.5 → incident=89.99999999999999。
- `metrics.query.tp99`：baseline=99.5 → incident=97.99999999999997。
- `metrics.query.cpu_usage`：baseline=0.002645259586665641 → incident=0.002679356302219246。
- `metrics.query.memory_usage`：baseline=0.093170166015625 → incident=0.093231201171875。
- `redis.hotkeys.hit_rate_percent`：baseline=26.956521739130434 → incident=41.37931034482758。
- `redis.memory.used_memory`：baseline=1412312.0 → incident=1416264.0。
- `rpc.metrics.timeout_rate`：baseline=None → incident=0.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.125。
- `rpc.metrics.latency_ms`：baseline=None → incident=3.296504。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=7.811206597938144。
- `rpc.metrics.call_volume`：baseline=None → incident=32。
- `cpu_usage` 趋势：{"samples": 17, "first": 0.00264819721122624, "last": 0.002679356302219246, "slope_per_second": 1.280779671712884e-07}。
- `memory_usage` 趋势：{"samples": 17, "first": 0.095062255859375, "last": 0.093231201171875, "slope_per_second": 1.3513502731821898e-06}。
真实采样数量：{"logs.query": 22, "traces.query": 16}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 64}
Engine 实际选择工具：metrics.query, logs.query, traces.query, rpc.metrics, changes.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `trace.span_error` (traces.query)：Trace 4db73b2cfb7ea4a6844dca8b36c04a8e path load-generator/frontend-proxy/frontend: status=ERROR, duration=77.4ms。
- Evidence `trace.span_error` (traces.query)：Trace 50cdb37af2d4f91ba96d1009d073e1b9 path load-generator/frontend-proxy/frontend/checkout/payment: status=ERROR, duration=0.3ms。
- Evidence `trace.span_error` (traces.query)：Trace c2fefe71d81a09f870204ee6cdebf4ba path load-generator/frontend-proxy/frontend/checkout/payment: status=ERROR, duration=0.6ms。
- Evidence `trace.span_error` (traces.query)：Trace 6f281fef20fe010594149774cb0243bb path load-generator: status=ERROR, duration=60.0ms。

#### dependency-unavailable #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/dependency-unavailable/repeat-01`。总实验时长 560.3 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.0 → incident=0.066670000166675。
- `metrics.query.error_rate`：baseline=0.0 → incident=100.0。
- `metrics.query.tp95`：baseline=960.0 → incident=4850.0。
- `metrics.query.tp99`：baseline=991.9999999999999 → incident=4970.0。
- `metrics.query.cpu_usage`：baseline=0.002711544197177021 → incident=0.002750271609400598。
- `metrics.query.memory_usage`：baseline=0.102874755859375 → incident=0.11212158203125。
- `redis.hotkeys.hit_rate_percent`：baseline=32.53968253968254 → incident=26.168224299065418。
- `redis.memory.used_memory`：baseline=1419512.0 → incident=1418192.0。
- `rpc.metrics.timeout_rate`：baseline=None → incident=0.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.125。
- `rpc.metrics.latency_ms`：baseline=None → incident=13.13624。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=16.853769721518987。
- `rpc.metrics.call_volume`：baseline=None → incident=32。
- `cpu_usage` 趋势：{"samples": 16, "first": 0.0027144896360464365, "last": 0.002750271609400598, "slope_per_second": 1.6757807277212947e-07}。
- `memory_usage` 趋势：{"samples": 16, "first": 0.095703125, "last": 0.11212158203125, "slope_per_second": 3.202550551470588e-05}。
真实采样数量：{"logs.query": 30, "traces.query": 15}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 88}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics, traces.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.iqr` (metrics.query)：error_rate: IQR level_shift, current=100.0, median=0.0, bounds=[-150.0, 250.0]。
- Evidence `trace.span_error` (traces.query)：Trace 4d3b8de27fbe59cd1ff4d1613b4a4ae1 path load-generator/frontend-proxy/frontend: status=ERROR, duration=3971.8ms。
- Evidence `trace.span_error` (traces.query)：Trace 1e2ceccf9b89170c70a81266b53952ed path load-generator/frontend-proxy/frontend: status=ERROR, duration=4209.9ms。
- Evidence `trace.span_error` (traces.query)：Trace 52bb4291afc368306cd9c4fc74f8c369 path load-generator/frontend-proxy/frontend: status=ERROR, duration=4155.9ms。

#### messaging-backlog #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/messaging-backlog/repeat-01`。总实验时长 521.1 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=404.0。
- `metrics.query.qps`：baseline=0.05000166672222408 → incident=0.01666722224074136。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=184.99999999999997 → incident=97.5。
- `metrics.query.tp99`：baseline=197.0 → incident=99.5。
- `metrics.query.cpu_usage`：baseline=0.0027984222223784655 → incident=0.002828298093047162。
- `metrics.query.memory_usage`：baseline=0.1041259765625 → incident=0.107635498046875。
- `redis.hotkeys.hit_rate_percent`：baseline=31.932773109243698 → incident=31.313131313131315。
- `redis.memory.used_memory`：baseline=1419136.0 → incident=1416384.0。
- `rpc.metrics.timeout_rate`：baseline=None → incident=0.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=None → incident=9.580248615384615。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=10.063179487179488。
- `rpc.metrics.call_volume`：baseline=None → incident=13。
- `cpu_usage` 趋势：{"samples": 16, "first": 0.0028011096552360477, "last": 0.002828298093047162, "slope_per_second": 1.2237637323836768e-07}。
- `memory_usage` 趋势：{"samples": 16, "first": 0.1060791015625, "last": 0.107635498046875, "slope_per_second": 3.441604913449755e-05}。
真实采样数量：{"logs.query": 38, "traces.query": 9}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 132}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics, traces.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=97.5, historical baselines={'last_week': None, 'yesterday': 208.5}。
- Evidence `anomaly.historical_baseline` (metrics.query)：qps: current=0.01666722224074136, historical baselines={'last_week': None, 'yesterday': 0.03333527790278638}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=99.5, historical baselines={'last_week': None, 'yesterday': 217.7}。
- Evidence `anomaly.volatility` (metrics.query)：tp99: rolling baseline std=32.6332, recent std=133.0822。

#### database-latency #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/database-latency/repeat-01`。总实验时长 566.0 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `db.slowlog.slow_query_count`：baseline=None → incident=21。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=1.0。
- `metrics.query.qps`：baseline=4.766905011917262 → incident=2.8167605586852895。
- `metrics.query.tp95`：baseline=434.99999999999983 → incident=15000.0。
- `metrics.query.tp99`：baseline=1151.9999999999975 → incident=15000.0。
- `metrics.query.cpu_usage`：baseline=0.24505718601535498 → incident=0.24607780235269827。
- `metrics.query.memory_usage`：baseline=0.816796875 → incident=0.8033203125。
- `redis.hotkeys.hit_rate_percent`：baseline=42.56756756756756 → incident=38.095238095238095。
- `redis.memory.used_memory`：baseline=1418528.0 → incident=1417376.0。
- `rpc.metrics.error_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=0.740096 → incident=1883.0495126887417。
- `rpc.metrics.baseline_latency_ms`：baseline=1.5329645714285713 → incident=4761.191026526316。
- `rpc.metrics.call_volume`：baseline=1 → incident=151。
- `cpu_usage` 趋势：{"samples": 17, "first": 0.24518957728109153, "last": 0.24607780235269827, "slope_per_second": 5.029773175517677e-06}。
- `memory_usage` 趋势：{"samples": 17, "first": 0.7099609375, "last": 0.8033203125, "slope_per_second": 0.000716305402369281}。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 44}
Engine 实际选择工具：metrics.query, traces.query, changes.query, rpc.metrics, logs.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.iqr` (metrics.query)：tp95: IQR level_shift, current=15000.0, median=494.9999999999998, bounds=[-21891.66666666667, 37135.0]。
- Evidence `anomaly.iqr` (metrics.query)：tp99: IQR level_shift, current=15000.0, median=1279.0000000000014, bounds=[-20570.00000000001, 36342.00000000001]。
- Evidence `trace.span_slow` (traces.query)：Trace 9cdb8d063a9553bba792aead27dd2b3d path frontend-proxy/frontend: status=UNSET, duration=24184.5ms。
- Evidence `trace.span_slow` (traces.query)：Trace 02539b11b483f52aed5cf4429a1121b9 path frontend-proxy/frontend: status=UNSET, duration=3028.2ms。

#### memory-growth #1

原始工件：`artifacts/otel_demo_benchmark/20261004T151904Z-91254d/memory-growth/repeat-01`。总实验时长 522.9 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.05000250012500625 → incident=0.05000166672222408。
- `metrics.query.tp95`：baseline=92.5 → incident=9.7。
- `metrics.query.tp99`：baseline=98.5 → incident=9.94。
- `metrics.query.cpu_usage`：baseline=9.086195758957764e-05 → incident=0.00030168406919407927。
- `metrics.query.memory_usage`：baseline=0.43234375 → incident=0.53390625。
- `redis.hotkeys.hit_rate_percent`：baseline=36.84210526315789 → incident=33.582089552238806。
- `redis.memory.used_memory`：baseline=1417704.0 → incident=1417416.0。
- `cpu_usage` 趋势：{"samples": 17, "first": 0.00011293949339641725, "last": 0.00030168406919407927, "slope_per_second": 7.844148635343e-07}。
- `memory_usage` 趋势：{"samples": 17, "first": 0.4388671875, "last": 0.53390625, "slope_per_second": 0.00036979804942810463}。
真实采样数量：{"logs.query": 18, "traces.query": 18}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 188}
Engine 实际选择工具：metrics.query, logs.query, rpc.metrics, traces.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `trace.span_slow` (traces.query)：Trace 603602d3012407614871520065de4592 path load-generator/frontend-proxy: status=UNSET, duration=1104.9ms。
- Evidence `trace.span_slow` (traces.query)：Trace 603602d3012407614871520065de4592 path load-generator/frontend-proxy: status=UNSET, duration=1105.1ms。
- Evidence `trace.span_slow` (traces.query)：Trace 603602d3012407614871520065de4592 path load-generator/frontend-proxy/frontend: status=UNSET, duration=1104.0ms。
- Evidence `trace.span_slow` (traces.query)：Trace 603602d3012407614871520065de4592 path load-generator/frontend-proxy/frontend: status=UNSET, duration=1104.6ms。

### 本次 Ground Truth mapping

| Scenario | flag/variant（仅 Controller） | operational category | root_service | mechanism / taxonomy note |
| --- | --- | --- | --- | --- |
| normal | all off | no_fault | frontend | all faults disabled under the same continuous load;  |
| compute | adHighCpu/on | resource_saturation | ad | CPU workload introduced in advertising service;  |
| payment-errors | paymentFailure/100% | rpc_error_rate | payment | rejected payment requests at a fixed probability of one;  |
| dependency-unavailable | paymentUnreachable/on | rpc_error_rate | payment | checkout directs payment RPC to an unreachable endpoint; Real smoke showed PaymentService/Charge ERROR with is_timeout=false; unreachable endpoint produces connection or DNS/UNAVAILABLE errors. Existing RPC_ERROR_RATE expresses the error symptom, not a dedicated downstream-unavailable mechanism. No new type or diagnosis rule added. |
| messaging-backlog | kafkaQueueProblems/on | kafka_consumer_lag | kafka | checkout amplifies Kafka message production and consumer delay;  |
| database-latency | productCatalogLockContention/on | db_slow_query | astronomy-db | periodic ACCESS EXCLUSIVE lock on catalog.products; Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these. |
| memory-growth | emailMemoryLeak/100x | resource_saturation | email | larger retained email deliveries create a rising memory footprint; Memory growth is a resource symptom; do not claim OOM unless independently observed. |
## smoke：20261004T145214Z-87d37f

Dataset `otel-demo-rca@1.0.0`，SHA-256 `07a549f0b445b6e2ea6c2fe4f10f89a918bff40acbf680048dfa79154c3954f1`；实际 repeat override=None，Normal 按 profile 真实重复。
Upstream `7ea09b865a9b9d411b59459b15133d84f8c2784a`；Demo 3.1.0；Collector 0.160.0。负载固定 5 用户、spawn rate 1。

| Scenario | 已执行/完整生命周期 | 故障效果确认 | Fault Top1 | Fault Top3 | Normal NO_FAULT | Gate pass | Budget exhausted | Degraded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 3/3 | 0 | N/A | N/A | 1/3 | 0/3 | 3/3 | 0/3 |
| compute | 1/1 | 1 | 0/1 | 1/1 | N/A | 0/1 | 1/1 | 0/1 |
| payment-errors | 1/0 | 0 | N/A | N/A | N/A | N/A | N/A | N/A |
| dependency-unavailable | 1/1 | 1 | 1/1 | 1/1 | N/A | 1/1 | 0/1 | 0/1 |
| messaging-backlog | 1/1 | 1 | 0/1 | 0/1 | N/A | 0/1 | 1/1 | 0/1 |
| database-latency | 1/1 | 1 | 0/1 | 0/1 | N/A | 0/1 | 1/1 | 0/1 |
| memory-growth | 1/1 | 0 | 0/1 | 0/1 | N/A | 0/1 | 1/1 | 0/1 |

Fault 总计：Top1 **1/5**，Top3 **2/5**；Normal **1/3**。
总体：完整生命周期 8/9，已评分 8；Gate pass 1/8，Budget exhausted 7/8，Degraded 0/8。
已确认产生可观测差异的 Fault 子集：Top1 1/4，Top3 2/4。未确认故障差异的分数仍在原始 score 中保存，但不能代表有效故障诊断准确率。

### 实际 trial、遥测与失败

| Case/repeat | 状态 | 实际预测 Top-K | tool/expert/rounds | RCA latency ms | 恢复 health/queries/symptom |
| --- | --- | --- | --- | --- | --- |
| normal/1 | completed | rpc_timeout | 6/1/4 | 5479.431 | True/True/N/A |
| normal/2 | completed | rpc_timeout | 6/1/4 | 5446.6140000000005 | True/True/N/A |
| normal/3 | completed | no_fault | 6/0/4 | 4067.6389999999997 | True/True/N/A |
| compute/1 | completed | rpc_timeout, resource_saturation | 5/1/4 | 5415.302 | True/True/N/A |
| payment-errors/1 | failed |  | N/A/N/A/N/A | N/A | True/True/N/A |
| dependency-unavailable/1 | completed | rpc_timeout, rpc_error_rate | 4/1/2 | 4652.827 | True/True/N/A |
| messaging-backlog/1 | completed | no_fault | 6/0/4 | 4151.954 | True/True/N/A |
| database-latency/1 | completed | rpc_timeout, rpc_error_rate, resource_saturation | 6/1/4 | 5302.016 | True/True/N/A |
| memory-growth/1 | completed | no_fault | 5/0/4 | 3766.444 | True/True/N/A |

#### normal #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/normal/repeat-01`。总实验时长 149.9 秒。
故障效果确认：None（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=10.500525026251312 → incident=9.700646709780653。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=212.4999999999985 → incident=49.397260273972634。
- `metrics.query.tp99`：baseline=1147.9999999999989 → incident=411.9999999999992。
- `metrics.query.cpu_usage`：baseline=0.02045402188593888 → incident=0.020500036190046457。
- `metrics.query.memory_usage`：baseline=0.705734375 → incident=0.704671875。
- `redis.hotkeys.hit_rate_percent`：baseline=33.333333333333336 → incident=39.814814814814824。
- `redis.memory.used_memory`：baseline=1416136.0 → incident=1415848.0。
- `rpc.metrics.error_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=514.26432 → incident=7.7364224。
- `rpc.metrics.baseline_latency_ms`：baseline=5.82133638095238 → incident=77.80395255172414。
- `rpc.metrics.call_volume`：baseline=1 → incident=40。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 0}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics, traces.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=411.9999999999992, historical baselines={'last_week': None, 'yesterday': 1000.7999999999989}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=49.397260273972634, historical baselines={'last_week': None, 'yesterday': 179.87945205479332}。

#### normal #2

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/normal/repeat-02`。总实验时长 150.9 秒。
故障效果确认：None（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=12.53354222570376 → incident=10.000333344444815。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=93.84615384615388 → incident=77.77777777777777。
- `metrics.query.tp99`：baseline=923.9999999999981 → incident=400.0。
- `metrics.query.cpu_usage`：baseline=0.02061893961920847 → incident=0.020658632554768394。
- `metrics.query.memory_usage`：baseline=0.704984375 → incident=0.70503125。
- `redis.hotkeys.hit_rate_percent`：baseline=40.44117647058824 → incident=38.4。
- `redis.memory.used_memory`：baseline=1416616.0 → incident=1416104.0。
- `rpc.metrics.timeout_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.error_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=22.8580352 → incident=11.93984。
- `rpc.metrics.baseline_latency_ms`：baseline=10.68990494117647 → incident=21.482302577777777。
- `rpc.metrics.call_volume`：baseline=5 → incident=50。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 0}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics, traces.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=400.0, historical baselines={'last_week': None, 'yesterday': 815.2000000000002}。

#### normal #3

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/normal/repeat-03`。总实验时长 149.2 秒。
故障效果确认：None（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=10.767025567518917 → incident=11.7339200293348。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=114.16666666666613 → incident=123.33333333333327。
- `metrics.query.tp99`：baseline=953.9999999999944 → incident=1048.0000000000055。
- `metrics.query.cpu_usage`：baseline=0.02078573601621903 → incident=0.020817889656527602。
- `metrics.query.memory_usage`：baseline=0.7048124999999998 → incident=0.7037343750000001。
- `redis.hotkeys.hit_rate_percent`：baseline=33.613445378151255 → incident=30.555555555555557。
- `redis.memory.used_memory`：baseline=1416680.0 → incident=1416648.0。
- `rpc.metrics.timeout_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.error_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=2.784512 → incident=50.01733008695652。
- `rpc.metrics.baseline_latency_ms`：baseline=5.221144 → incident=10.307904。
- `rpc.metrics.call_volume`：baseline=1 → incident=46。
真实采样数量：{"logs.query": 50, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 4}
Engine 实际选择工具：metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query；Controller 的全工具采集不计入 RCA tool_calls。

#### compute #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/compute/repeat-01`。总实验时长 197.4 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.2500083336111204 → incident=0.2500125006250313。
- `metrics.query.tp95`：baseline=1.9 → incident=20.000000000000036。
- `metrics.query.tp99`：baseline=1.98 → incident=43.99999999999998。
- `metrics.query.cpu_usage`：baseline=0.00010645659232447969 → incident=0.0578064138234002。
- `redis.hotkeys.hit_rate_percent`：baseline=39.83739837398374 → incident=41.35338345864662。
- `redis.memory.used_memory`：baseline=1416616.0 → incident=1418312.0。
真实采样数量：{"logs.query": 44, "traces.query": 28}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 12}
Engine 实际选择工具：metrics.query, logs.query, rpc.metrics, traces.query, changes.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `anomaly.historical_baseline` (metrics.query)：cpu_usage: current=0.0578064138234002, historical baselines={'last_week': None, 'yesterday': 0.009719168480402409}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=43.99999999999998, historical baselines={'last_week': None, 'yesterday': 9.27333333333333}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=20.000000000000036, historical baselines={'last_week': None, 'yesterday': 5.033333333333339}。

#### payment-errors #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/payment-errors/repeat-01`。总实验时长 101.3 秒。
失败：{"type": "RuntimeError", "message": "Baseline metrics/traces absent"}
隔离审计：{"passed": true, "control_records_removed": 12}

#### dependency-unavailable #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/dependency-unavailable/repeat-01`。总实验时长 197.5 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=2.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.03333444448148272 → incident=0.0333350000833375。
- `metrics.query.error_rate`：baseline=0.0 → incident=50.0。
- `metrics.query.tp95`：baseline=380.0 → incident=15000.0。
- `metrics.query.tp99`：baseline=396.0 → incident=15000.0。
- `metrics.query.cpu_usage`：baseline=0.0022362832133407664 → incident=0.0022557955184887594。
- `metrics.query.memory_usage`：baseline=0.0916748046875 → incident=0.09576416015625。
- `redis.hotkeys.hit_rate_percent`：baseline=31.132075471698112 → incident=43.08943089430894。
- `redis.memory.used_memory`：baseline=1416712.0 → incident=1417672.0。
- `rpc.metrics.timeout_rate`：baseline=None → incident=0.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.047619047619047616。
- `rpc.metrics.latency_ms`：baseline=None → incident=74.22370133333334。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=9.916721548387097。
- `rpc.metrics.call_volume`：baseline=None → incident=21。
真实采样数量：{"logs.query": 10, "traces.query": 5}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 40}
Engine 实际选择工具：metrics.query, logs.query, changes.query, rpc.metrics；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `rpc.timeout` (rpc.metrics)：RPC timeout rate=0.0%, latency ratio=7.5x。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp99: current=15000.0, historical baselines={'last_week': None, 'yesterday': 2996.0}。
- Evidence `anomaly.historical_baseline` (metrics.query)：error_rate: current=50.0, historical baselines={'last_week': None, 'yesterday': 8.333333333333334}。
- Evidence `anomaly.historical_baseline` (metrics.query)：tp95: current=15000.0, historical baselines={'last_week': None, 'yesterday': 2980.0}。

#### messaging-backlog #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/messaging-backlog/repeat-01`。总实验时长 195.8 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=302.0。
- `metrics.query.qps`：baseline=0.05000250012500625 → incident=0.05000083334722245。
- `metrics.query.error_rate`：baseline=0.0 → incident=0.0。
- `metrics.query.tp95`：baseline=185.0 → incident=184.99999999999997。
- `metrics.query.tp99`：baseline=197.0 → incident=197.0。
- `metrics.query.cpu_usage`：baseline=0.0022842700150888886 → incident=0.0023016137049908045。
- `metrics.query.memory_usage`：baseline=0.099212646484375 → incident=0.09698486328125。
- `redis.hotkeys.hit_rate_percent`：baseline=33.33333333333333 → incident=43.75。
- `redis.memory.used_memory`：baseline=1416504.0 → incident=1417048.0。
- `rpc.metrics.timeout_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.error_rate`：baseline=0.0 → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=2.4291669333333332 → incident=8.442279384615384。
- `rpc.metrics.baseline_latency_ms`：baseline=11.609442461538462 → incident=4.81611776。
- `rpc.metrics.call_volume`：baseline=15 → incident=26。
真实采样数量：{"logs.query": 37, "traces.query": 6}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 90}
Engine 实际选择工具：metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query；Controller 的全工具采集不计入 RCA tool_calls。

#### database-latency #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/database-latency/repeat-01`。总实验时长 200.2 秒。
故障效果确认：True（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `db.slowlog.slow_query_count`：baseline=None → incident=24。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=3.5834527817593926 → incident=2.4000800026667557。
- `metrics.query.tp95`：baseline=262.49999999999864 → incident=13799.999999999984。
- `metrics.query.tp99`：baseline=734.285714285714 → incident=15000.0。
- `metrics.query.cpu_usage`：baseline=0.21766351603515516 → incident=0.21862679933889184。
- `metrics.query.memory_usage`：baseline=0.8064453125 → incident=0.9732421875。
- `redis.hotkeys.hit_rate_percent`：baseline=37.5 → incident=31.250000000000004。
- `redis.memory.used_memory`：baseline=1416472.0 → incident=1415000.0。
- `rpc.metrics.error_rate`：baseline=None → incident=0.0。
- `rpc.metrics.latency_ms`：baseline=None → incident=14149.321332363637。
- `rpc.metrics.baseline_latency_ms`：baseline=None → incident=1680.2352347919464。
- `rpc.metrics.call_volume`：baseline=None → incident=11。
真实采样数量：{"logs.query": 49, "traces.query": 50}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 34}
Engine 实际选择工具：metrics.query, traces.query, changes.query, rpc.metrics, logs.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。
- Evidence `trace.span_slow` (traces.query)：Trace a45fc8c440e37b23e2cd9878c886452c path load-generator/frontend-proxy/frontend/product-catalog: status=UNSET, duration=2960.1ms。
- Evidence `trace.span_slow` (traces.query)：Trace 3b21d767249039f4147181e2a835c781 path frontend-proxy/frontend/recommendation: status=UNSET, duration=11394.9ms。
- Evidence `trace.span_slow` (traces.query)：Trace c590b5dd2a78e6a8850e6505e23d3145 path load-generator/frontend-proxy/frontend/product-catalog: status=UNSET, duration=29195.5ms。
- Evidence `trace.span_slow` (traces.query)：Trace 7a170ba1b97c4ee1e5d4021676af33f6 path frontend-proxy/frontend/product-catalog: status=UNSET, duration=27213.8ms。

#### memory-growth #1

原始工件：`artifacts/otel_demo_benchmark/20261004T145214Z-87d37f/memory-growth/repeat-01`。总实验时长 193.8 秒。
故障效果确认：False（Controller 的观测检查，不用于 Agent 预测）。
- `db.connections.max_connections`：baseline=100.0 → incident=100.0。
- `kafka.lag.consumer_lag`：baseline=0.0 → incident=0.0。
- `metrics.query.qps`：baseline=0.03333444448148272 → incident=0.01666750004166875。
- `metrics.query.tp95`：baseline=7.8 → incident=7.9。
- `metrics.query.tp99`：baseline=7.96 → incident=7.9799999999999995。
- `metrics.query.cpu_usage`：baseline=0.008929725972963258 → incident=0.0089537543788648。
- `metrics.query.memory_usage`：baseline=0.7576171875 → incident=0.7580078125。
- `redis.hotkeys.hit_rate_percent`：baseline=40.625 → incident=27.272727272727277。
- `redis.memory.used_memory`：baseline=1415800.0 → incident=1414680.0。
真实采样数量：{"logs.query": 1, "traces.query": 1}；其他全部 normalized/domain 数据及原始请求参见 telemetry/。
隔离审计：{"passed": true, "control_records_removed": 40}
Engine 实际选择工具：metrics.query, logs.query, traces.query, changes.query, topology.query；Controller 的全工具采集不计入 RCA tool_calls。

### 本次 Ground Truth mapping

| Scenario | flag/variant（仅 Controller） | operational category | root_service | mechanism / taxonomy note |
| --- | --- | --- | --- | --- |
| normal | all off | no_fault | frontend | all faults disabled under the same continuous load;  |
| compute | adHighCpu/on | resource_saturation | ad | CPU workload introduced in advertising service;  |
| payment-errors | paymentFailure/100% | rpc_error_rate | payment | rejected payment requests at a fixed probability of one;  |
| dependency-unavailable | paymentUnreachable/on | rpc_timeout | payment | checkout directs payment RPC to an unreachable endpoint; Provisional nearest existing downstream-unavailable category; connection refusal is not equivalent to timeout. Verify actual RPC outcomes and report mismatch. |
| messaging-backlog | kafkaQueueProblems/on | kafka_consumer_lag | kafka | checkout amplifies Kafka message production and consumer delay;  |
| database-latency | productCatalogLockContention/on | db_slow_query | astronomy-db | periodic ACCESS EXCLUSIVE lock on catalog.products; Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these. |
| memory-growth | emailMemoryLeak/100x | resource_saturation | email | larger retained email deliveries create a rising memory footprint; Memory growth is a resource symptom; do not claim OOM unless independently observed. |

## 隔离、复现与限制

Alert 由 service_name、alert_type、severity、description allowlist 单独构造，随机 ID、无 signals/labels、无 scenario 或答案。工具参数仅含此 Alert；控制元数据及明确注入日志在 ToolResult/Evidence 前过滤，邮箱脱敏，原始响应仅在 Controller 工件保存。现有 changes.query 不访问 flagd。泄漏自动测试与实际每次运行的断言均执行，审计记录过滤数量。
Fault Controller 使用已核对 upstream 原子 write-then-rename API，独立 runtime flags 副本；写入后轮询 flagd Connect evaluation 的 variant，保存 before/requested/confirmed。各 trial 前后及异常路径 reset，锁防止并发注入；最后 final/flags.json 保存关闭状态。正常退出已关闭全部故障。email 的人工恢复重启与实际 OOM 区分。
RCA 使用现有 OpsPilotWorkflow/AdaptiveInvestigator，LLM 禁用、fallback 保持原逻辑。没有每场景诊断规则，没有根据答案修改 confidence，没有调整 Gate/Ranker/Planner/Runtime。评分只比较 category；root_service 存档，不宣称精确服务定位。
`telemetry/raw/`、prometheus_queries、normalized metrics/logs/traces、rca/report/events/tool_results/evidence、benchmark/ground_truth/score 和 environment/source archive 可复查每个判对/错。fixtures 标明实际采集源、SHA、时间、scenario 和 artifact；未修改实测值制造理想答案。
telemetry gap：无真实部署/事件/topology inventory；单 PostgreSQL 无 replica；active_connections 缺失；Valkey maxmemory/百分比与 hotkeys 缺失；部分 RPC 缺协议状态，不能伪造 timeout=0；ad 缺 container resource series，CPU 使用 JVM 比例。每次 trace/log 有窗口和采样限额，未观察不等于全量系统中绝不存在。
窗口限制：Controller 保存故障启用前的独立 baseline；Engine 沿用现有 provider 的告警前历史窗口，其中包含故障稳定阶段，因而部分历史基准已混入故障观测。两者不能混同，后续应独立消融窗口设计，不能据此回写本次预测或分数。
taxonomy gap：downstream-unavailable 的 UNAVAILABLE/连接/DNS 错误不等于 timeout，应核对实际 span/log。DB flag 的机制为锁竞争，而 DB_SLOW_QUERY 表示数据库延迟/瓶颈；当前 normalized Tools 不稳定暴露锁等待身份，未新增 DB_LOCK_CONTENTION 或专用规则。memory trend 不等于 OOM。
背景环境 gap：upstream Locust 包含可选 agent 的 /prompt 请求，但 core/full/observability 没有部署可选 AI layer，存在背景请求失败，未调用真实 LLM 补齐该服务。Normal ground truth 针对 frontend 的无注入状态，不宣称所有后台请求均无错误；fixed load/task mix 在同一 profile 全部案例保持相同。未来可在独立版本控制这项因素，不回写本次结果。
下一阶段消融需先评估 Normal false positives、降速/延迟变化的证据方向、独立 Evidence 来源和 Gate 预算、域工具/服务 scope 与 taxonomy gap。不可通过场景关键词或答案调规则。单次 Fault 和重复 Normal 仅为初步结果；release 默认支持 Fault repeat>=3，但本次实际次数以工件为准，未伪造三次重复。

测试和开发期中断/失败记录见 artifacts/otel_demo_benchmark*_tests.txt、probe/smoke 日志；正式统计仅使用上述明确列出的完整 runs。

## 最终复核：20261004T151904Z-91254d

复核时间 `2026-10-04T16:29:42.042487+00:00`：28 个容器健康/运行=True；活动故障=[]；upstream status=''。
与运行开始时源码归档比较，59 个非 Benchmark 文件变化=[]。原有工作区修改仍保留，此检查不表示相对于 git HEAD 干净。
Alert/Tool arguments/ToolResult/Evidence/report 共 112 份 JSON 通过最终 key/value 隔离检查；该复核使用当前更严格的检查器，原始运行源码仍保存在 source/。

测试：252 passed、4 skipped、1 warning；pytest exit=0，Ruff exit=0。
13 份完整生命周期真实录制 fixture 用于离线回放；回放测试不计入 live 分数。原始测试日志 `artifacts/otel_demo_benchmark_tests_final.txt`。
