# Evaluation Report

- Evaluation: `20261003T142504Z-opspilot_adaptive_planner-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_adaptive_planner`

## Metrics

- Root Cause Hit@1: 0.286 (6/21)
- Root Cause Hit@3: 0.286 (6/21)
- Evidence Recall (macro): 0.285714
- Tool Success Rate: 1.000 (144/144)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 0.89 ms
- Average Tool / Expert calls: 5.76 / 0.08
- Average Investigation rounds: 3.92
- Planner Action Valid Rate: 1.000 (146/146)
- Duplicate Action Rate: 0.000 (0/146)
- Budget Exhaustion Rate: 0.960 (24/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-dev-01, db-dev-02, db-dev-03, db-dev-04, redis-dev-01, redis-dev-02, redis-dev-03, redis-dev-04, kafka-dev-01, kafka-dev-02, kafka-dev-03, kafka-dev-04, rpc-dev-01, rpc-dev-02, rpc-dev-04

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
