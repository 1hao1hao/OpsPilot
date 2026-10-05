# Formal release artifact retention

The completed release is `20261005T041333Z-9b9d16`: 63 actual diagnoses,
21 complete paired lifecycles and 18 confirmed fault/recovery lifecycles.
An interrupted lifecycle with no diagnosis is retained separately; it does
not increase the number of diagnoses. The earlier experiment 1.0.0 remains
an independent interrupted run and is excluded from release scores.

Git contains compact configuration, source hashes, records, metrics, failure
analysis and independent acceptance summaries. Full telemetry, API payloads,
per-mode evidence, controller history and archived source remain in the local
artifact directories; the release directory alone is approximately 2.8 GiB.
They were audited locally before completion, including 504 engine payloads
and API-key absence in 5,862 JSON files. Ignored files were not deleted.

See [the complete report](../../reports/otel_demo_rca_v1.md) and
[reproduction instructions](../../benchmarks/datasets/otel_demo/v1/EXPERIMENT.md).
Reproducing a run requires a working Docker daemon, the pinned official Demo,
real backend telemetry and a separately configured model API key. Secrets,
virtual environments and external upstream clones do not belong in Git.
