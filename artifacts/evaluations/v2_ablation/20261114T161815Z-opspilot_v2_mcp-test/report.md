# Evaluation Report

- Evaluation: `20261114T161815Z-opspilot_v2_mcp-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `opspilot_v2_mcp`

## Metrics

- Root Cause Hit@1: 0.300 (3/10)
- Root Cause Hit@3: 0.300 (3/10)
- Confirmed coverage / false confirmation / abstention: 0.000 (0/10) / n/a / 1.000 (12/12)
- Requirement coverage / task completion: 0.000 (0/38) / 0.000 (0/38)
- Tool selection recall / precision: 0.154 (2/13) / 0.028 (2/72)
- Repeated tool-target calls: 0
- Evidence Recall (macro): 0.3
- Tool Success Rate: 1.000 (72/72)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 458.05 ms
- P50 latency: 12.712 ms
- Average Tool / Expert calls: 6.0 / 1.0
- Average Investigation rounds: 3.333333
- Planner Action Valid Rate: 1.000 (72/72)
- Duplicate Action Rate: 0.000 (0/72)
- Budget Exhaustion Rate: 0.000 (0/12)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-test-01, db-test-02, redis-test-01, redis-test-02, kafka-test-01, kafka-test-02, platform-test-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
