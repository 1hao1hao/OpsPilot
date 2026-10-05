# Artifact-based failure analysis

自动规则只分类已观测条件，不调用 LLM，不证明唯一因果；所有 causal diagnosis 仍为 unknown。

## full_adaptive / normal #1
预测 ['rpc_timeout']；期望 no_fault；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-01\modes\full_adaptive\rca\events.json`。

## fixed_full / normal #1
预测 ['redis_low_hit_rate', 'redis_memory_pressure', 'rpc_timeout']；期望 no_fault；primary=unknown；causal diagnosis=unknown。

## adaptive_no_l2 / normal #1
预测 ['rpc_timeout']；期望 no_fault；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\normal\repeat-01\modes\adaptive_no_l2\rca\events.json`。

## fixed_full / normal #2
预测 ['redis_low_hit_rate', 'redis_memory_pressure']；期望 no_fault；primary=unknown；causal diagnosis=unknown。

## fixed_full / normal #3
预测 ['redis_low_hit_rate', 'redis_memory_pressure']；期望 no_fault；primary=unknown；causal diagnosis=unknown。

## full_adaptive / messaging-backlog #1
预测 ['rpc_timeout']；期望 kafka_consumer_lag；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-01\modes\full_adaptive\rca\events.json`。

## fixed_full / messaging-backlog #1
预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']；期望 kafka_consumer_lag；primary=C；causal diagnosis=unknown。
- Evidence 未提取：{'observed_consumer_lag': 707.0, 'frozen_evidence_threshold': 1000, 'condition': 'numeric backlog sampled; frozen converter suppresses expected evidence below threshold', 'rule_artifact': 'D:\\deeprca\\DeepRCA-Agent-master\\artifacts\\otel_demo_rca_v1\\20261005T041333Z-9b9d16\\source\\src\\opspilot\\evidence\\collector.py'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-01\modes\fixed_full\rca\tool_results.json`。

## adaptive_no_l2 / messaging-backlog #1
预测 ['rpc_timeout']；期望 kafka_consumer_lag；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-01\modes\adaptive_no_l2\rca\events.json`。

## fixed_full / messaging-backlog #2
预测 ['redis_low_hit_rate', 'redis_memory_pressure']；期望 kafka_consumer_lag；primary=C；causal diagnosis=unknown。
- Evidence 未提取：{'observed_consumer_lag': 808.0, 'frozen_evidence_threshold': 1000, 'condition': 'numeric backlog sampled; frozen converter suppresses expected evidence below threshold', 'rule_artifact': 'D:\\deeprca\\DeepRCA-Agent-master\\artifacts\\otel_demo_rca_v1\\20261005T041333Z-9b9d16\\source\\src\\opspilot\\evidence\\collector.py'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-02\modes\fixed_full\rca\tool_results.json`。

## adaptive_no_l2 / messaging-backlog #2
预测 ['no_fault']；期望 kafka_consumer_lag；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-02\modes\adaptive_no_l2\rca\events.json`。

## full_adaptive / messaging-backlog #2
预测 ['no_fault']；期望 kafka_consumer_lag；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-02\modes\full_adaptive\rca\events.json`。

## adaptive_no_l2 / messaging-backlog #3
预测 ['rpc_timeout', 'resource_saturation']；期望 kafka_consumer_lag；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-03\modes\adaptive_no_l2\rca\events.json`。

## full_adaptive / messaging-backlog #3
预测 ['rpc_timeout', 'resource_saturation']；期望 kafka_consumer_lag；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-03\modes\full_adaptive\rca\events.json`。

## fixed_full / messaging-backlog #3
预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']；期望 kafka_consumer_lag；primary=C；causal diagnosis=unknown。
- Evidence 未提取：{'observed_consumer_lag': 606.0, 'frozen_evidence_threshold': 1000, 'condition': 'numeric backlog sampled; frozen converter suppresses expected evidence below threshold', 'rule_artifact': 'D:\\deeprca\\DeepRCA-Agent-master\\artifacts\\otel_demo_rca_v1\\20261005T041333Z-9b9d16\\source\\src\\opspilot\\evidence\\collector.py'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\messaging-backlog\repeat-03\modes\fixed_full\rca\tool_results.json`。

## full_adaptive / database-latency #1
预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']；期望 db_slow_query；primary=F；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\full_adaptive\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\scenario.json`。
- Planner 未采集区分性工具：{'uncollected_source_with_actual_expected_supporting_evidence': ['db.slowlog'], 'certainty': 'omitted observation, not proof of an illegal planner action or guaranteed correction', 'no_l2_budget_constraint': False}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\fixed_full\rca\evidence.json`。

## fixed_full / database-latency #1
预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']；期望 db_slow_query；primary=D；causal diagnosis=unknown。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\scenario.json`。
- Ranking 错误：{'expected_supporting_evidence_exists_but_top1_differs': 'db_slow_query', 'supporting_evidence_ids': ['ev-9f9dabc8a0b7e5a4'], 'certainty': 'observed ranking condition; not proof that ranking is the sole cause'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\fixed_full\rca\evidence.json`。

## adaptive_no_l2 / database-latency #1
预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']；期望 db_slow_query；primary=F；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\adaptive_no_l2\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\scenario.json`。
- Planner 未采集区分性工具：{'uncollected_source_with_actual_expected_supporting_evidence': ['db.slowlog'], 'certainty': 'omitted observation, not proof of an illegal planner action or guaranteed correction', 'no_l2_budget_constraint': True}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-01\modes\fixed_full\rca\evidence.json`。

## fixed_full / database-latency #2
预测 ['rpc_timeout', 'rpc_error_rate', 'redis_low_hit_rate']；期望 db_slow_query；primary=D；causal diagnosis=unknown。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\scenario.json`。
- Ranking 错误：{'expected_supporting_evidence_exists_but_top1_differs': 'db_slow_query', 'supporting_evidence_ids': ['ev-5fb2af336861d325'], 'certainty': 'observed ranking condition; not proof that ranking is the sole cause'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\fixed_full\rca\evidence.json`。

## adaptive_no_l2 / database-latency #2
预测 ['rpc_timeout', 'rpc_error_rate']；期望 db_slow_query；primary=F；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\adaptive_no_l2\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\scenario.json`。
- Planner 未采集区分性工具：{'uncollected_source_with_actual_expected_supporting_evidence': ['db.slowlog'], 'certainty': 'omitted observation, not proof of an illegal planner action or guaranteed correction', 'no_l2_budget_constraint': True}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\fixed_full\rca\evidence.json`。

## full_adaptive / database-latency #2
预测 ['rpc_timeout', 'rpc_error_rate']；期望 db_slow_query；primary=F；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\full_adaptive\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\scenario.json`。
- Planner 未采集区分性工具：{'uncollected_source_with_actual_expected_supporting_evidence': ['db.slowlog'], 'certainty': 'omitted observation, not proof of an illegal planner action or guaranteed correction', 'no_l2_budget_constraint': False}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-02\modes\fixed_full\rca\evidence.json`。

## adaptive_no_l2 / database-latency #3
预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']；期望 db_slow_query；primary=F；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\adaptive_no_l2\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\scenario.json`。
- Planner 未采集区分性工具：{'uncollected_source_with_actual_expected_supporting_evidence': ['db.slowlog'], 'certainty': 'omitted observation, not proof of an illegal planner action or guaranteed correction', 'no_l2_budget_constraint': True}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\fixed_full\rca\evidence.json`。

## full_adaptive / database-latency #3
预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']；期望 db_slow_query；primary=F；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\full_adaptive\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\scenario.json`。
- Planner 未采集区分性工具：{'uncollected_source_with_actual_expected_supporting_evidence': ['db.slowlog'], 'certainty': 'omitted observation, not proof of an illegal planner action or guaranteed correction', 'no_l2_budget_constraint': False}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\fixed_full\rca\evidence.json`。

## fixed_full / database-latency #3
预测 ['rpc_timeout', 'rpc_error_rate', 'resource_saturation']；期望 db_slow_query；primary=D；causal diagnosis=unknown。
- Taxonomy gap：{'mapping_note': 'Mechanism is lock contention; category is database latency or bottleneck. Existing normalized telemetry does not expose lock wait identity; do not equate these.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\scenario.json`。
- Ranking 错误：{'expected_supporting_evidence_exists_but_top1_differs': 'db_slow_query', 'supporting_evidence_ids': ['ev-78b86b79d94b4e8f'], 'certainty': 'observed ranking condition; not proof that ranking is the sole cause'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\database-latency\repeat-03\modes\fixed_full\rca\evidence.json`。

## full_adaptive / memory-growth #1
预测 ['rpc_timeout', 'resource_saturation']；期望 resource_saturation；primary=D；causal diagnosis=unknown。
- Ranking 错误：{'expected_is_lower_rank': 'resource_saturation', 'supporting_evidence_ids': ['ev-9bae95b0fb294a43']}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\full_adaptive\rca\evidence.json`。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\full_adaptive\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Memory growth is a resource symptom; do not claim OOM unless independently observed.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\scenario.json`。

## fixed_full / memory-growth #1
预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']；期望 resource_saturation；primary=D；causal diagnosis=unknown。
- Taxonomy gap：{'mapping_note': 'Memory growth is a resource symptom; do not claim OOM unless independently observed.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\scenario.json`。
- Ranking 错误：{'expected_supporting_evidence_exists_but_top1_differs': 'resource_saturation', 'supporting_evidence_ids': ['ev-9bae95b0fb294a43'], 'certainty': 'observed ranking condition; not proof that ranking is the sole cause'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\fixed_full\rca\evidence.json`。

## adaptive_no_l2 / memory-growth #1
预测 ['rpc_timeout', 'resource_saturation']；期望 resource_saturation；primary=D；causal diagnosis=unknown。
- Ranking 错误：{'expected_is_lower_rank': 'resource_saturation', 'supporting_evidence_ids': ['ev-9bae95b0fb294a43']}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\adaptive_no_l2\rca\evidence.json`。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\modes\adaptive_no_l2\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Memory growth is a resource symptom; do not claim OOM unless independently observed.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-01\scenario.json`。

## adaptive_no_l2 / memory-growth #3
预测 ['rpc_timeout', 'resource_saturation']；期望 resource_saturation；primary=D；causal diagnosis=unknown。
- Ranking 错误：{'expected_is_lower_rank': 'resource_saturation', 'supporting_evidence_ids': ['ev-95189db815f87ef1', 'ev-2dca7c42cca28120']}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\adaptive_no_l2\rca\evidence.json`。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\adaptive_no_l2\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Memory growth is a resource symptom; do not claim OOM unless independently observed.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\scenario.json`。

## full_adaptive / memory-growth #3
预测 ['rpc_timeout', 'resource_saturation']；期望 resource_saturation；primary=D；causal diagnosis=unknown。
- Ranking 错误：{'expected_is_lower_rank': 'resource_saturation', 'supporting_evidence_ids': ['ev-95189db815f87ef1', 'ev-2dca7c42cca28120']}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\full_adaptive\rca\evidence.json`。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\full_adaptive\rca\events.json`。
- Taxonomy gap：{'mapping_note': 'Memory growth is a resource symptom; do not claim OOM unless independently observed.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\scenario.json`。

## fixed_full / memory-growth #3
预测 ['rpc_timeout', 'resource_saturation', 'redis_low_hit_rate']；期望 resource_saturation；primary=D；causal diagnosis=unknown。
- Ranking 错误：{'expected_is_lower_rank': 'resource_saturation', 'supporting_evidence_ids': ['ev-95189db815f87ef1', 'ev-2dca7c42cca28120']}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\modes\fixed_full\rca\evidence.json`。
- Taxonomy gap：{'mapping_note': 'Memory growth is a resource symptom; do not claim OOM unless independently observed.', 'causal_role': 'unproven'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\memory-growth\repeat-03\scenario.json`。
