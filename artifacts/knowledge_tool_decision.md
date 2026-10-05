# Knowledge Tool Decision

INCONCLUSIVE

尚无经核验的历史 incident/runbook 证明增量区分信息；本轮还存在工具选择、排名、Gate、预算、采样/窗口或 taxonomy 限制，不能从错例直接推断需要 RAG。
来源 `artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16`，实际失败 Top1 29 次。

## 判断标准

必须同时满足：多个真实错例；正常采到 metrics/logs/traces；实时 Evidence 无法区分；独立历史 incident/runbook 确有区分信息；不是简单 Tool/Evidence/Planner bug。未核验的条件记为未证实，不当作已满足。

## 当前 Case / Evidence / 错误条件

- full_adaptive/normal #1：预测 ['rpc_timeout']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-01\modes\full_adaptive`。
  Evidence `anomaly.historical_baseline`：tp99: current=280.9999999999992, historical baselines={'last_week': None, 'yesterday': 189.5166666666673}。
- fixed_full/normal #1：预测 ['redis_low_hit_rate', 'redis_memory_pressure', 'rpc_timeout']，观测条件 ['unknown']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-01\modes\fixed_full`。
  Evidence `redis.hit_rate`：Redis hit rate is 33.3%。
  Evidence `anomaly.historical_baseline`：tp99: current=280.9999999999992, historical baselines={'last_week': None, 'yesterday': 189.5166666666673}。
- adaptive_no_l2/normal #1：预测 ['rpc_timeout']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-01\modes\adaptive_no_l2`。
  Evidence `anomaly.historical_baseline`：tp99: current=280.9999999999992, historical baselines={'last_week': None, 'yesterday': 189.5166666666673}。
- fixed_full/normal #2：预测 ['redis_low_hit_rate', 'redis_memory_pressure']，观测条件 ['unknown']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-02\modes\fixed_full`。
  Evidence `redis.hit_rate`：Redis hit rate is 36.7%。
- fixed_full/normal #3：预测 ['redis_low_hit_rate', 'redis_memory_pressure']，观测条件 ['unknown']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-03\modes\fixed_full`。
  Evidence `redis.hit_rate`：Redis hit rate is 33.6%。
- full_adaptive/messaging-backlog #1：预测 ['rpc_timeout']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-01\modes\full_adaptive`。
  Evidence `anomaly.historical_baseline`：tp99: current=791.9999999999999, historical baselines={'last_week': None, 'yesterday': 494.5}。
  Evidence `anomaly.historical_baseline`：tp95: current=760.0, historical baselines={'last_week': None, 'yesterday': 472.5}。
  Evidence `anomaly.iqr`：tp95: IQR spike, current=760.0, median=380.0, bounds=[201.25, 651.25]。
- fixed_full/messaging-backlog #1：预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']，观测条件 ['C']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-01\modes\fixed_full`。
  Evidence `anomaly.historical_baseline`：tp99: current=791.9999999999999, historical baselines={'last_week': None, 'yesterday': 494.5}。
  Evidence `redis.hit_rate`：Redis hit rate is 35.7%。
  Evidence `anomaly.historical_baseline`：tp95: current=760.0, historical baselines={'last_week': None, 'yesterday': 472.5}。
- adaptive_no_l2/messaging-backlog #1：预测 ['rpc_timeout']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-01\modes\adaptive_no_l2`。
  Evidence `anomaly.historical_baseline`：tp99: current=791.9999999999999, historical baselines={'last_week': None, 'yesterday': 494.5}。
  Evidence `anomaly.historical_baseline`：tp95: current=760.0, historical baselines={'last_week': None, 'yesterday': 472.5}。
  Evidence `anomaly.iqr`：tp95: IQR spike, current=760.0, median=380.0, bounds=[201.25, 651.25]。
- fixed_full/messaging-backlog #2：预测 ['redis_low_hit_rate', 'redis_memory_pressure']，观测条件 ['C']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-02\modes\fixed_full`。
  Evidence `redis.hit_rate`：Redis hit rate is 21.0%。
- adaptive_no_l2/messaging-backlog #2：预测 ['no_fault']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-02\modes\adaptive_no_l2`。
- full_adaptive/messaging-backlog #2：预测 ['no_fault']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-02\modes\full_adaptive`。
- adaptive_no_l2/messaging-backlog #3：预测 ['rpc_timeout', 'resource_saturation']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-03\modes\adaptive_no_l2`。
  Evidence `anomaly.historical_baseline`：tp95: current=740.0, historical baselines={'last_week': None, 'yesterday': 410.7142857142857}。
  Evidence `anomaly.historical_baseline`：qps: current=0.05000250012500625, historical baselines={'last_week': None, 'yesterday': 0.03809764566270958}。
  Evidence `anomaly.historical_baseline`：tp99: current=788.0, historical baselines={'last_week': None, 'yesterday': 428.80952380952374}。
- full_adaptive/messaging-backlog #3：预测 ['rpc_timeout', 'resource_saturation']，观测条件 ['G']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-03\modes\full_adaptive`。
  Evidence `anomaly.historical_baseline`：tp95: current=740.0, historical baselines={'last_week': None, 'yesterday': 410.7142857142857}。
  Evidence `anomaly.historical_baseline`：qps: current=0.05000250012500625, historical baselines={'last_week': None, 'yesterday': 0.03809764566270958}。
  Evidence `anomaly.historical_baseline`：tp99: current=788.0, historical baselines={'last_week': None, 'yesterday': 428.80952380952374}。
- fixed_full/messaging-backlog #3：预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']，观测条件 ['C']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-03\modes\fixed_full`。
  Evidence `redis.hit_rate`：Redis hit rate is 32.5%。
  Evidence `anomaly.historical_baseline`：tp95: current=740.0, historical baselines={'last_week': None, 'yesterday': 410.7142857142857}。
  Evidence `anomaly.historical_baseline`：qps: current=0.05000250012500625, historical baselines={'last_week': None, 'yesterday': 0.03809764566270958}。
- full_adaptive/database-latency #1：预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']，观测条件 ['G', 'I', 'F']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\full_adaptive`。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=41.66666666666666, bounds=[-22401.826923076926, 37441.096153846156]。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=48.42191780821918, bounds=[-22379.166666666668, 37427.5]。
  Evidence `trace.span_slow`：Trace 5b03c1b025f1b8b7ede447503ea204ae path load-generator: status=UNSET, duration=15012.1ms。
- fixed_full/database-latency #1：预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']，观测条件 ['I', 'D']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\fixed_full`。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=41.66666666666666, bounds=[-22401.826923076926, 37441.096153846156]。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=48.42191780821918, bounds=[-22379.166666666668, 37427.5]。
  Evidence `trace.span_slow`：Trace 5b03c1b025f1b8b7ede447503ea204ae path load-generator: status=UNSET, duration=15012.1ms。
- adaptive_no_l2/database-latency #1：预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']，观测条件 ['G', 'I', 'F']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\adaptive_no_l2`。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=41.66666666666666, bounds=[-22401.826923076926, 37441.096153846156]。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=48.42191780821918, bounds=[-22379.166666666668, 37427.5]。
  Evidence `trace.span_slow`：Trace 5b03c1b025f1b8b7ede447503ea204ae path load-generator: status=UNSET, duration=15012.1ms。
- fixed_full/database-latency #2：预测 ['rpc_timeout', 'rpc_error_rate', 'redis_low_hit_rate']，观测条件 ['I', 'D']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\fixed_full`。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=58.50000000000021, bounds=[-22378.560975609755, 37427.13658536585]。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=45.72881355932203, bounds=[-22392.804878048784, 37435.68292682927]。
  Evidence `trace.span_slow`：Trace ec32200190cb64f43c600f591beb94b2 path frontend-proxy/frontend: status=UNSET, duration=3052.3ms。
- adaptive_no_l2/database-latency #2：预测 ['rpc_timeout', 'rpc_error_rate']，观测条件 ['G', 'I', 'F']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\adaptive_no_l2`。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=58.50000000000021, bounds=[-22378.560975609755, 37427.13658536585]。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=45.72881355932203, bounds=[-22392.804878048784, 37435.68292682927]。
  Evidence `trace.span_slow`：Trace ec32200190cb64f43c600f591beb94b2 path frontend-proxy/frontend: status=UNSET, duration=3052.3ms。
- full_adaptive/database-latency #2：预测 ['rpc_timeout', 'rpc_error_rate']，观测条件 ['G', 'I', 'F']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\full_adaptive`。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=58.50000000000021, bounds=[-22378.560975609755, 37427.13658536585]。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=45.72881355932203, bounds=[-22392.804878048784, 37435.68292682927]。
  Evidence `trace.span_slow`：Trace ec32200190cb64f43c600f591beb94b2 path frontend-proxy/frontend: status=UNSET, duration=3052.3ms。
- adaptive_no_l2/database-latency #3：预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']，观测条件 ['G', 'I', 'F']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\adaptive_no_l2`。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=43.02777777777777, bounds=[-22396.048387096773, 37437.62903225806]。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=48.605555555555554, bounds=[-22379.209677419356, 37427.52580645161]。
  Evidence `trace.span_slow`：Trace 6011ff764eb1f210a6540eeb10ce9af4 path load-generator/frontend-proxy/frontend/product-catalog: status=UNSET, duration=28280.0ms。
- full_adaptive/database-latency #3：预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']，观测条件 ['G', 'I', 'F']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\full_adaptive`。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=43.02777777777777, bounds=[-22396.048387096773, 37437.62903225806]。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=48.605555555555554, bounds=[-22379.209677419356, 37427.52580645161]。
  Evidence `trace.span_slow`：Trace 6011ff764eb1f210a6540eeb10ce9af4 path load-generator/frontend-proxy/frontend/product-catalog: status=UNSET, duration=28280.0ms。
- fixed_full/database-latency #3：预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']，观测条件 ['I', 'D']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\fixed_full`。
  Evidence `anomaly.iqr`：tp95: IQR level_shift, current=15000.0, median=43.02777777777777, bounds=[-22396.048387096773, 37437.62903225806]。
  Evidence `anomaly.iqr`：tp99: IQR level_shift, current=15000.0, median=48.605555555555554, bounds=[-22379.209677419356, 37427.52580645161]。
  Evidence `trace.span_slow`：Trace 6011ff764eb1f210a6540eeb10ce9af4 path load-generator/frontend-proxy/frontend/product-catalog: status=UNSET, duration=28280.0ms。
- full_adaptive/memory-growth #1：预测 ['rpc_timeout', 'resource_saturation']，观测条件 ['D', 'G', 'I']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\full_adaptive`。
  Evidence `anomaly.historical_baseline`：tp99: current=49.46666666666666, historical baselines={'last_week': None, 'yesterday': 105.44761904761903}。
  Evidence `anomaly.historical_baseline`：qps: current=0.06667111140742717, historical baselines={'last_week': None, 'yesterday': 0.04206585992791856}。
  Evidence `anomaly.historical_baseline`：tp95: current=47.33333333333333, historical baselines={'last_week': None, 'yesterday': 98.66666666666666}。
- fixed_full/memory-growth #1：预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']，观测条件 ['I', 'D']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\fixed_full`。
  Evidence `anomaly.historical_baseline`：tp99: current=49.46666666666666, historical baselines={'last_week': None, 'yesterday': 105.44761904761903}。
  Evidence `anomaly.historical_baseline`：qps: current=0.06667111140742717, historical baselines={'last_week': None, 'yesterday': 0.04206585992791856}。
  Evidence `anomaly.historical_baseline`：tp95: current=47.33333333333333, historical baselines={'last_week': None, 'yesterday': 98.66666666666666}。
- adaptive_no_l2/memory-growth #1：预测 ['rpc_timeout', 'resource_saturation']，观测条件 ['D', 'G', 'I']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\adaptive_no_l2`。
  Evidence `anomaly.historical_baseline`：tp99: current=49.46666666666666, historical baselines={'last_week': None, 'yesterday': 105.44761904761903}。
  Evidence `anomaly.historical_baseline`：qps: current=0.06667111140742717, historical baselines={'last_week': None, 'yesterday': 0.04206585992791856}。
  Evidence `anomaly.historical_baseline`：tp95: current=47.33333333333333, historical baselines={'last_week': None, 'yesterday': 98.66666666666666}。
- adaptive_no_l2/memory-growth #3：预测 ['rpc_timeout', 'resource_saturation']，观测条件 ['D', 'G', 'I']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\adaptive_no_l2`。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout/shipping: status=UNSET, duration=1030.8ms。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout: status=UNSET, duration=1065.5ms。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout/shipping: status=UNSET, duration=1021.6ms。
- full_adaptive/memory-growth #3：预测 ['rpc_timeout', 'resource_saturation']，观测条件 ['D', 'G', 'I']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\full_adaptive`。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout/shipping: status=UNSET, duration=1030.8ms。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout: status=UNSET, duration=1065.5ms。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout/shipping: status=UNSET, duration=1021.6ms。
- fixed_full/memory-growth #3：预测 ['rpc_timeout', 'resource_saturation', 'redis_low_hit_rate']，观测条件 ['D', 'I']；Evidence 与 facts 见 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\fixed_full`。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout/shipping: status=UNSET, duration=1030.8ms。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout: status=UNSET, duration=1065.5ms。
  Evidence `trace.span_slow`：Trace d7ba996ae3491caa9dae0852940d7bb3 path load-generator/frontend-proxy/frontend/checkout/shipping: status=UNSET, duration=1021.6ms。

## 什么外部知识可能帮助

DB 锁等待 runbook 与独立历史 incident 的锁/SQL 区分模式
consumer group/service dependency 文档

这些是待核验候选，没有独立 incident/runbook corpus，也未证明实时 Evidence 不够。不能用已知注入机制作为实时 Agent 的答案。

## 后续验证

先修复并冻结 telemetry/window/tool selection/ranking 问题，再核验独立脱敏知识文档；若五项标准满足，单独做 Without/With Knowledge，比较 Top1/Top3、Tool Cost、Latency，知识不能直接决定 RootCause。

不直接实现 EvalRAG/Knowledge Tool。

复核：Fixed 真实 Kafka lag 为 707/808/606，均低于冻结 Evidence 的 1000 阈值；DB slowlog 计数 27/22/16 已形成支持 Evidence，但正确类别未进入 Top3。Full/No-L2 未采集 DB slowlog。以上基础阈值、工具选择和排名条件尚未排除，不能据此证明需要历史知识；结论仍为 INCONCLUSIVE。
