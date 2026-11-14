# Evaluation Report

- Evaluation: `20261114T161809Z-opspilot_hybrid-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `opspilot_hybrid`

## Metrics

- Root Cause Hit@1: 0.200 (2/10)
- Root Cause Hit@3: 0.200 (2/10)
- Confirmed coverage / false confirmation / abstention: 0.000 (0/10) / n/a / 1.000 (12/12)
- Requirement coverage / task completion: 0.027 (1/37) / n/a
- Tool selection recall / precision: 0.231 (3/13) / 0.044 (3/68)
- Repeated tool-target calls: 0
- Evidence Recall (macro): 0.2
- Tool Success Rate: 1.000 (68/68)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 1.666 ms
- P50 latency: 1.137 ms
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
