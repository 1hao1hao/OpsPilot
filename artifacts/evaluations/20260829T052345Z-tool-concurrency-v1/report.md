# Tool Execution Concurrency Report

- Evaluation: `20260829T052345Z-tool-concurrency-v1`
- Dataset: `opspilot-rca@1.0.0` `dev`
- Cases / repeats: 24 / 3
- Fixed async provider delay per tool: 20.0 ms

| Mode | Runs | P50 | P95 | Tool Success |
|---|---:|---:|---:|---:|
| Sequential | 72 | 201.909 ms | 202.531 ms | 720/720 |
| Parallel | 72 | 20.819 ms | 21.135 ms | 720/720 |

P95 speedup: **9.583x**.

Every measured run is retained in `runs.jsonl`. This controlled benchmark measures scheduling behavior,
not production network latency.
