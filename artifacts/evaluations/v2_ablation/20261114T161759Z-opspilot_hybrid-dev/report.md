# Evaluation Report

- Evaluation: `20261114T161759Z-opspilot_hybrid-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_hybrid`

## Metrics

- Root Cause Hit@1: 0.286 (6/21)
- Root Cause Hit@3: 0.286 (6/21)
- Confirmed coverage / false confirmation / abstention: 0.000 (0/21) / n/a / 1.000 (25/25)
- Requirement coverage / task completion: 0.053 (4/76) / n/a
- Tool selection recall / precision: 0.357 (10/28) / 0.068 (10/146)
- Repeated tool-target calls: 0
- Evidence Recall (macro): 0.285714
- Tool Success Rate: 1.000 (146/146)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 1.692 ms
- P50 latency: 1.156 ms
- Average Tool / Expert calls: 5.84 / 0.08
- Average Investigation rounds: 4.0
- Planner Action Valid Rate: 1.000 (148/148)
- Duplicate Action Rate: 0.000 (0/148)
- Budget Exhaustion Rate: 1.000 (25/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-dev-01, db-dev-02, db-dev-03, db-dev-04, redis-dev-01, redis-dev-02, redis-dev-03, redis-dev-04, kafka-dev-01, kafka-dev-02, kafka-dev-03, kafka-dev-04, rpc-dev-01, rpc-dev-02, rpc-dev-04

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
