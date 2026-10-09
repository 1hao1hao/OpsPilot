# Incident-local evidence: 18/18 target

**ACCURACY_TARGET_NOT_MET**

| Mode | Fault Top1 | Fault Top3 | Normal | Avg tools | P50 / P95 ms |
|---|---:|---:|---:|---:|---:|
| full_adaptive | 16/18 | 16/18 | 3/3 | 5.48 | 13694 / 18703 |
| fixed_full | 17/18 | 17/18 | 3/3 | 13.00 | 26089 / 28697 |
| adaptive_no_l2 | 14/18 | 14/18 | 3/3 | 5.71 | 13705 / 15084 |

Fresh run `artifacts/otel_demo_rca_v1/20261009T043206Z-54bfc1`: 21 recovered lifecycles, 18 observed faults, 63 actual diagnoses. Full uses 57.88% fewer tools than Fixed. Rules and configuration were frozen before the run.

Previous fresh release: Full Fault Top1 16/18, Normal 2/3. Its report remains in accuracy_closure.md. Development-only replay of those observations reaches 18/18 and Normal 3/3; it is not the result in the table above.

The two previous fault failures were caused by isolated slow database calls. One was 181 seconds before the alert. The Normal timeout false positive asserted timeout despite an observed zero timeout rate.

Database spans now need incident-local corroboration from like calls: at least three slow calls and a group mean exceeding the existing one-second latency budget. Older or isolated spans remain diagnostic hints without causal votes. Database domain evidence uses the same corroboration. Timestamp-less legacy snapshots retain their previous behavior; absence of history is not treated as proof of a healthy baseline. This cohort rule does not establish causal certainty and may miss rare-tail incidents.

An observed zero timeout rate blocks a timeout inference from latency growth. Actual protocol timeouts still contribute. Planner, Gate, Ranker, scorer, labels, fault controls, load, budgets and rotating mode order remain unchanged. No scenario-specific diagnosis rules or ground truth at the Engine boundary.

Validation: 301 passed, 4 skipped; Ruff and independent audit passed. All faults off; 28 services healthy/running. Actual API requests, responses and usage retained. Raw telemetry remains local; this is a small controlled demo benchmark, not a production accuracy guarantee.

## Remaining observed errors

- full_adaptive / messaging-backlog repeat 1: expected kafka_consumer_lag, predicted ['rpc_timeout'].
- adaptive_no_l2 / messaging-backlog repeat 1: expected kafka_consumer_lag, predicted ['rpc_timeout'].
- adaptive_no_l2 / messaging-backlog repeat 2: expected kafka_consumer_lag, predicted ['no_fault'].
- adaptive_no_l2 / messaging-backlog repeat 3: expected kafka_consumer_lag, predicted ['no_fault'].
- full_adaptive / memory-growth repeat 1: expected resource_saturation, predicted ['no_fault'].
- fixed_full / memory-growth repeat 1: expected resource_saturation, predicted ['no_fault'].
- adaptive_no_l2 / memory-growth repeat 1: expected resource_saturation, predicted ['no_fault'].

[Metrics](../artifacts/otel_demo_rca_v1/20261009T043206Z-54bfc1/metrics.json) | [Verification](../artifacts/otel_demo_rca_v1/20261009T043206Z-54bfc1/final_verification.json) | [Independent audit](../artifacts/otel_demo_rca_v1/20261009T043206Z-54bfc1/independent_audit.json)

```powershell
. scripts/otel_demo/local_live_env.ps1
python -m opspilot.otel_benchmark.experiment --config benchmarks/datasets/otel_demo/v1/top1_18.yaml
python scripts/otel_demo/finalize_top1_18.py artifacts/otel_demo_rca_v1/<new-run>
```
