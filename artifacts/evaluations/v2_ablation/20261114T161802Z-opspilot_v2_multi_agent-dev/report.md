# Evaluation Report

- Evaluation: `20261114T161802Z-opspilot_v2_multi_agent-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_v2_multi_agent`

## Metrics

- Root Cause Hit@1: 0.381 (8/21)
- Root Cause Hit@3: 0.381 (8/21)
- Confirmed coverage / false confirmation / abstention: 0.000 (0/21) / n/a / 1.000 (25/25)
- Requirement coverage / task completion: 0.038 (3/78) / 0.037 (3/80)
- Tool selection recall / precision: 0.321 (9/28) / 0.059 (9/152)
- Repeated tool-target calls: 0
- Evidence Recall (macro): 0.380952
- Tool Success Rate: 1.000 (154/154)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 5.785 ms
- P50 latency: 4.257 ms
- Average Tool / Expert calls: 6.16 / 1.04
- Average Investigation rounds: 3.28
- Planner Action Valid Rate: 1.000 (154/154)
- Duplicate Action Rate: 0.000 (0/154)
- Budget Exhaustion Rate: 0.000 (0/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

db-dev-01, db-dev-02, db-dev-03, db-dev-04, redis-dev-01, redis-dev-02, redis-dev-03, redis-dev-04, kafka-dev-01, kafka-dev-02, kafka-dev-03, kafka-dev-04, platform-dev-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
