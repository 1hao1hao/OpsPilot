# Accuracy closure: development record

Historical source: `artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16`.
These are development replays, **not fresh accuracy results**. No missing tool
calls are synthesized. The ground truth and scorer remain unchanged.

| Iteration | Full Fault Top1 | Fixed Fault Top1 | Full Normal | Fixed Normal |
|---|---:|---:|---:|---:|
| Original | 10/18 | 10/18 | 2/3 | 0/3 |
| 1: evidence specificity and source caps | 10/18 | 6/18 | 3/3 | 0/3 |
| 2: reconstructed temporal context | 12/18 | 14/18 | 3/3 | 3/3 |
| 3: reject insignificant resource noise | 15/18 | 18/18 | 3/3 | 3/3 |

Iteration 1 removes unlimited repeated-span confidence votes, distinguishes typed
database query latency from generic slow RPC spans, rejects decreasing latency,
and removes the unsupported inference that a low cache hit rate proves memory
pressure. Fixed temporarily regresses because its old scalar Redis observations
still lack historical context. Regression also exposed an overly narrow interpretation
of untyped database dependency failures; iteration 2 restores their alternative
hypotheses so the expert can check replication, queries and connections.

Iteration 2 recovers only existing original raw Prometheus time series, retaining
the same tool inventory per investigation. Queue growth and cache degradation
require sustained changes above baseline noise. Low stable cache efficiency does
not establish a new incident. Typed slow database spans establish query latency.
The observation window prefers reference samples ending five minutes before the
alert; short histories use their earliest third. A local reference is not a
guaranteed healthy baseline. Missing historical samples remain unknown.
Resource growth initially introduces false positives on small memory changes
during RPC failures. Planner context now retains distinct evidence types rather
than twenty nearly identical spans. Observed messaging operations are diagnostic
hints with no supported root cause; they do not prove a Kafka fault.

Iteration 3 requires a sustained resource increase of more than five percentage
points, ten percent relative change, and six median absolute deviations. It also
requires a material temporal change before historical/IQR/volatility detectors
can vote on live observations. Cache efficiency evidence receives less causal
weight than directly observed pressure/growth. This removes the new resource
false positives while retaining memory growth in all three historical repeats.
All remaining Full replay failures lack `kafka.lag`; replay cannot repair tool
selection and is not counted as new formal accuracy.

Only these three general rule iterations are permitted. Freeze the final source
manifest before the fresh paired release. Retain all failed attempts and explicit
target failure if Full does not achieve at least 17/18.
