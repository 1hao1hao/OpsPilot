# Artifact-based failure analysis

以下为观测条件，不是 LLM 猜测或确定的唯一原因。

## full_adaptive / normal #1
预测 ['rpc_timeout']；期望 no_fault；primary=E；causal diagnosis=unknown。
- Gate 接受错误 Top1：{'accepted_top1': 'rpc_timeout', 'expected': 'no_fault'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T032959Z-d57607\normal\repeat-01\modes\full_adaptive\rca\events.json`。

## fixed_full / normal #1
预测 ['rpc_timeout', 'redis_low_hit_rate', 'redis_memory_pressure']；期望 no_fault；primary=E；causal diagnosis=unknown。
- Gate 接受错误 Top1：{'accepted_top1': 'rpc_timeout', 'expected': 'no_fault'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T032959Z-d57607\normal\repeat-01\modes\fixed_full\rca\events.json`。

## adaptive_no_l2 / normal #1
预测 ['rpc_timeout']；期望 no_fault；primary=E；causal diagnosis=unknown。
- Gate 接受错误 Top1：{'accepted_top1': 'rpc_timeout', 'expected': 'no_fault'}；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T032959Z-d57607\normal\repeat-01\modes\adaptive_no_l2\rca\events.json`。

## fixed_full / normal #2
预测 ['redis_low_hit_rate', 'redis_memory_pressure']；期望 no_fault；primary=unknown；causal diagnosis=unknown。

## adaptive_no_l2 / normal #3
预测 ['rpc_timeout']；期望 no_fault；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T032959Z-d57607\normal\repeat-03\modes\adaptive_no_l2\rca\events.json`。

## full_adaptive / normal #3
预测 ['rpc_timeout']；期望 no_fault；primary=G；causal diagnosis=unknown。
- Budget 耗尽：The final gate decision explicitly records exhausted budget；artifact `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T032959Z-d57607\normal\repeat-03\modes\full_adaptive\rca\events.json`。

## fixed_full / normal #3
预测 ['redis_low_hit_rate', 'redis_memory_pressure', 'rpc_timeout']；期望 no_fault；primary=unknown；causal diagnosis=unknown。
