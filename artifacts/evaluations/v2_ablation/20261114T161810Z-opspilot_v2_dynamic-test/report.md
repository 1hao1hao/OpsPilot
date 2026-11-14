# Evaluation Report

- Evaluation: `20261114T161810Z-opspilot_v2_dynamic-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `opspilot_v2_dynamic`

## Metrics

- Root Cause Hit@1: 0.300 (3/10)
- Root Cause Hit@3: 0.300 (3/10)
- Confirmed coverage / false confirmation / abstention: 0.000 (0/10) / n/a / 1.000 (12/12)
- Requirement coverage / task completion: 0.000 (0/38) / 0.000 (0/42)
- Tool selection recall / precision: 0.154 (2/13) / 0.029 (2/68)
- Repeated tool-target calls: 0
- Evidence Recall (macro): 0.3
- Tool Success Rate: 1.000 (68/68)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 9.029 ms
- P50 latency: 5.764 ms
- Average Tool / Expert calls: 5.666667 / 1.0
- Average Investigation rounds: 4.0
- Planner Action Valid Rate: 1.000 (68/68)
- Duplicate Action Rate: 0.000 (0/68)
- Budget Exhaustion Rate: 0.000 (0/12)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-test-01, db-test-02, redis-test-01, redis-test-02, kafka-test-01, kafka-test-02, platform-test-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
