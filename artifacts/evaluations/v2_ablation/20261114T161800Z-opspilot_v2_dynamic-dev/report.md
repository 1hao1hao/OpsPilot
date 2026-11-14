# Evaluation Report

- Evaluation: `20261114T161800Z-opspilot_v2_dynamic-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_v2_dynamic`

## Metrics

- Root Cause Hit@1: 0.333 (7/21)
- Root Cause Hit@3: 0.381 (8/21)
- Confirmed coverage / false confirmation / abstention: 0.000 (0/21) / n/a / 1.000 (25/25)
- Requirement coverage / task completion: 0.026 (2/78) / 0.023 (2/88)
- Tool selection recall / precision: 0.321 (9/28) / 0.062 (9/145)
- Repeated tool-target calls: 0
- Evidence Recall (macro): 0.333333
- Tool Success Rate: 1.000 (145/145)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 8.91 ms
- P50 latency: 5.129 ms
- Average Tool / Expert calls: 5.8 / 1.0
- Average Investigation rounds: 4.0
- Planner Action Valid Rate: 1.000 (145/145)
- Duplicate Action Rate: 0.000 (0/145)
- Budget Exhaustion Rate: 0.000 (0/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-dev-01, db-dev-02, db-dev-03, db-dev-04, redis-dev-01, redis-dev-02, redis-dev-03, redis-dev-04, kafka-dev-01, kafka-dev-02, kafka-dev-03, kafka-dev-04, platform-dev-01, adaptive-db-dev-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
