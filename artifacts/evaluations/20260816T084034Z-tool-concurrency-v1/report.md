# Tool Execution Concurrency Report

- Evaluation: `20260816T084034Z-tool-concurrency-v1`
- Dataset: `opspilot-rca@1.0.0` `dev`
- Cases / repeats: 24 / 3
- Fixed async provider delay per tool: 20.0 ms

| Mode | Runs | P50 | P95 | Tool Success |
|---|---:|---:|---:|---:|
| Sequential | 72 | 220.672 ms | 498.469 ms | 648/648 |
| Parallel | 72 | 20.869 ms | 99.827 ms | 648/648 |

P95 speedup: **4.993x**.

Every measured run is retained in `runs.jsonl`. This controlled benchmark measures scheduling behavior,
not production network latency.
