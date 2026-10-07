# RCA Accuracy Closure

**ACCURACY_TARGET_NOT_MET**

Fresh run: `artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e`. 21 completed baseline/fault/reset/recovery lifecycles, 18 observed and recovered faults, 63 actual investigations. Same ground truth, scoring, five-user official Locust load, budgets and model configuration as the earlier formal release.

| Mode | Fault Top1 | Fault Top3 | Normal | Avg tools | P50 / P95 ms |
|---|---:|---:|---:|---:|---:|
| full_adaptive | 16/18 | 18/18 | 2/3 | 5.67 | 13567 / 17849 |
| fixed_full | 16/18 | 18/18 | 1/3 | 13.00 | 25497 / 28380 |
| adaptive_no_l2 | 13/18 | 15/18 | 2/3 | 5.71 | 13250 / 15121 |

| Scenario | Before Full Top1 | Fresh Full Top1 | Fresh Fixed Top1 |
|---|---:|---:|---:|
| compute | 3/3 | 3/3 | 3/3 |
| database-latency | 0/3 | 3/3 | 3/3 |
| dependency-unavailable | 3/3 | 2/3 | 2/3 |
| memory-growth | 1/3 | 3/3 | 3/3 |
| messaging-backlog | 0/3 | 3/3 | 3/3 |
| normal | 2/3 | 2/3 | 1/3 |
| payment-errors | 3/3 | 2/3 | 2/3 |

Full tool calls decrease by 56.41% relative to Fixed. P50/P95 measure individual RCA execution, excluding lifecycle warmup/stabilization/recovery. Model cost is not estimated; actual request/response/usage and fallback records are retained.

## General mechanisms and attribution limits

1. Preserve temporal observations; compare robust historical and recent windows, retaining unknown when baseline samples are absent. Queue growth below the old absolute threshold can now become evidence; stable low cache hit rates are suppressed.
2. Require material sustained resource growth and directional deterioration. Falling latency and small resource variation cannot vote for a new fault.
3. Distinguish typed database query latency from long successful spans. Long spans alone remain diagnostic context; protocol timeout status and baseline latency degradation are separate evidence.
4. Weight specific measurements above propagated symptoms, split ambiguous hypotheses and cap repeated observations per tool/source. Additional distinct tools provide bounded corroboration; distinct tools are not necessarily independent physical backends, so this is not a calibrated causal probability.
5. Retain distinct evidence types in the bounded Planner context. Messaging producer/consumer operations are routing hints without root-cause support. No domain tool is added to the seed or executed unconditionally.

The three development replays and their reasons are in [accuracy_closure_development.md](accuracy_closure_development.md). Replay results cannot establish fresh accuracy or repair missing tool selection. Before/after experiments support the combined change; there is no fresh per-mechanism ablation establishing unique causality.

## Remaining observed failures

- full_adaptive / normal repeat 1: expected `no_fault`, predicted `['db_slow_query']`; fault evidence causes a Normal false positive. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, db.slowlog.
- fixed_full / normal repeat 1: expected `no_fault`, predicted `['db_slow_query']`; fault evidence causes a Normal false positive. Tools: alerts.query, changes.query, db.connections, db.replication, db.slowlog, kafka.lag, logs.query, metrics.query, redis.hotkeys, redis.memory, rpc.metrics, topology.query, traces.query.
- adaptive_no_l2 / normal repeat 1: expected `no_fault`, predicted `['db_slow_query']`; fault evidence causes a Normal false positive. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query.
- fixed_full / normal repeat 3: expected `no_fault`, predicted `['rpc_timeout']`; fault evidence causes a Normal false positive. Tools: alerts.query, changes.query, db.connections, db.replication, db.slowlog, kafka.lag, logs.query, metrics.query, redis.hotkeys, redis.memory, rpc.metrics, topology.query, traces.query.
- full_adaptive / payment-errors repeat 1: expected `rpc_error_rate`, predicted `['db_slow_query', 'rpc_error_rate']`; ranking despite supporting evidence. Tools: metrics.query, logs.query, traces.query, topology.query, changes.query, rpc.metrics.
- fixed_full / payment-errors repeat 1: expected `rpc_error_rate`, predicted `['db_slow_query', 'rpc_error_rate']`; ranking despite supporting evidence. Tools: alerts.query, changes.query, db.connections, db.replication, db.slowlog, kafka.lag, logs.query, metrics.query, redis.hotkeys, redis.memory, rpc.metrics, topology.query, traces.query.
- adaptive_no_l2 / payment-errors repeat 1: expected `rpc_error_rate`, predicted `['db_slow_query', 'rpc_error_rate']`; ranking despite supporting evidence. Tools: metrics.query, logs.query, traces.query, topology.query, changes.query, alerts.query.
- full_adaptive / dependency-unavailable repeat 1: expected `rpc_error_rate`, predicted `['db_slow_query', 'rpc_error_rate', 'rpc_timeout']`; ranking despite supporting evidence. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, db.slowlog.
- fixed_full / dependency-unavailable repeat 1: expected `rpc_error_rate`, predicted `['db_slow_query', 'rpc_error_rate', 'rpc_timeout']`; ranking despite supporting evidence. Tools: alerts.query, changes.query, db.connections, db.replication, db.slowlog, kafka.lag, logs.query, metrics.query, redis.hotkeys, redis.memory, rpc.metrics, topology.query, traces.query.
- adaptive_no_l2 / dependency-unavailable repeat 1: expected `rpc_error_rate`, predicted `['db_slow_query', 'rpc_error_rate', 'rpc_timeout']`; ranking despite supporting evidence. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query.
- adaptive_no_l2 / messaging-backlog repeat 1: expected `kafka_consumer_lag`, predicted `['no_fault']`; no supporting evidence in gathered observations. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query.
- adaptive_no_l2 / messaging-backlog repeat 2: expected `kafka_consumer_lag`, predicted `['no_fault']`; no supporting evidence in gathered observations. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query.
- adaptive_no_l2 / messaging-backlog repeat 3: expected `kafka_consumer_lag`, predicted `['no_fault']`; no supporting evidence in gathered observations. Tools: metrics.query, logs.query, changes.query, traces.query, topology.query, alerts.query.

## Integrity and reproduction

No scenario IDs or injection flag names in diagnosis rules. Ground truth/control metadata is guarded at tool and LLM boundaries and scored only after diagnosis. Benchmark scorer and dataset bytes match the old frozen run. Every mode shares the same alert window, with rotating mode order. No Adaptive all-tool invocation. Frozen source/config hashes, actual calls, secret absence, all faults off and 28 running services pass independent audit.

Validation: 288 passed, 4 skipped; Ruff passed. Raw telemetry is retained locally; compact configurations, scores, metrics, failure conditions and verification are versioned.

```powershell
. scripts/otel_demo/local_live_env.ps1
python -m opspilot.otel_benchmark.experiment --config benchmarks/datasets/otel_demo/v1/accuracy_closure.yaml
python scripts/otel_demo/finalize_accuracy_closure.py artifacts/otel_demo_rca_v1/<new-run>
```

[Actual records](../artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e/records.json) · [Frozen configuration](../artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e/config.json) · [Independent audit](../artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e/independent_audit.json) · [Final verification](../artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e/final_verification.json)

## Measured residual failure conditions

These measurements explain limitations of the frozen rules; they do not modify the recorded outcomes.

- full_adaptive / normal repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 4/31; error/timeout spans 0.
- fixed_full / normal repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 4/31; error/timeout spans 0.
- adaptive_no_l2 / normal repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 4/31; error/timeout spans 0.
- fixed_full / normal repeat 3: Supporting facts and ranking remain in failure_analysis.json; unique causal attribution is unresolved. DB slow spans 0/21; error/timeout spans 0.
- full_adaptive / payment-errors repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 1/49; error/timeout spans 56.
- fixed_full / payment-errors repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 1/49; error/timeout spans 56.
- adaptive_no_l2 / payment-errors repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 1/49; error/timeout spans 56.
- full_adaptive / dependency-unavailable repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 1/48; error/timeout spans 90.
- fixed_full / dependency-unavailable repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 1/48; error/timeout spans 90.
- adaptive_no_l2 / dependency-unavailable repeat 1: Typed slow DB spans use an absolute 1s duration threshold; their evidence lacks baseline-change/prevalence validation and receives direct-evidence priority. DB slow spans 1/48; error/timeout spans 90.
- adaptive_no_l2 / messaging-backlog repeat 1: No-L2 deliberately excludes the domain lag measurement. DB slow spans 0/87; error/timeout spans 0.
- adaptive_no_l2 / messaging-backlog repeat 2: No-L2 deliberately excludes the domain lag measurement. DB slow spans 0/94; error/timeout spans 0.
- adaptive_no_l2 / messaging-backlog repeat 3: No-L2 deliberately excludes the domain lag measurement. DB slow spans 0/82; error/timeout spans 0.

[Measured counts and time offsets](../artifacts/otel_demo_rca_v1/20261007T134537Z-17c43e/failure_conditions.json). No additional rule iteration is performed after the three permitted development iterations.
