# Evaluation Report

- Evaluation: `20260812T040942Z-deeprca_baseline-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 24
- System: `deeprca_baseline`

## Metrics

- Root Cause Hit@1: 0.300 (6/20)
- Root Cause Hit@3: 0.300 (6/20)
- Evidence Recall (macro): 0.0
- Tool Success Rate: n/a
- E2E Success Rate: 1.000 (24/24)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 0.006 ms

## Failures

db-dev-01, db-dev-02, db-dev-03, db-dev-04, redis-dev-02, redis-dev-03, redis-dev-04, kafka-dev-01, kafka-dev-02, kafka-dev-03, kafka-dev-04, rpc-dev-03, rpc-dev-04, platform-dev-03

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
