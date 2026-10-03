# Evaluation Report

- Evaluation: `20261003T142505Z-opspilot_adaptive_without_dynamic_l2-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_adaptive_without_dynamic_l2`

## Metrics

- Root Cause Hit@1: 0.238 (5/21)
- Root Cause Hit@3: 0.286 (6/21)
- Evidence Recall (macro): 0.238095
- Tool Success Rate: 1.000 (144/144)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 1.033 ms
- Average Tool / Expert calls: 5.76 / 0.0
- Average Investigation rounds: 4.0
- Planner Action Valid Rate: 1.000 (144/144)
- Duplicate Action Rate: 0.000 (0/144)
- Budget Exhaustion Rate: 1.000 (25/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-dev-01, db-dev-02, db-dev-03, db-dev-04, redis-dev-01, redis-dev-02, redis-dev-03, redis-dev-04, kafka-dev-01, kafka-dev-02, kafka-dev-03, kafka-dev-04, rpc-dev-01, rpc-dev-02, rpc-dev-04, adaptive-db-dev-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
