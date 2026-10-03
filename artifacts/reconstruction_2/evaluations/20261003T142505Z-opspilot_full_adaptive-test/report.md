# Evaluation Report

- Evaluation: `20261003T142505Z-opspilot_full_adaptive-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `opspilot_full_adaptive`

## Metrics

- Root Cause Hit@1: 0.200 (2/10)
- Root Cause Hit@3: 0.200 (2/10)
- Evidence Recall (macro): 0.2
- Tool Success Rate: 1.000 (68/68)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 0.911 ms
- Average Tool / Expert calls: 5.666667 / 0.0
- Average Investigation rounds: 4.0
- Planner Action Valid Rate: 1.000 (68/68)
- Duplicate Action Rate: 0.000 (0/68)
- Budget Exhaustion Rate: 1.000 (12/12)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-test-01, db-test-02, redis-test-01, redis-test-02, kafka-test-01, kafka-test-02, rpc-test-01, rpc-test-02

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
