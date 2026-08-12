# Evaluation Report

- Evaluation: `20260812T041314Z-deeprca_baseline-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `deeprca_baseline`

## Metrics

- Root Cause Hit@1: 0.100 (1/10)
- Root Cause Hit@3: 0.100 (1/10)
- Evidence Recall (macro): 0.0
- Tool Success Rate: n/a
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 0.018 ms

## Failures

db-test-01, db-test-02, redis-test-02, kafka-test-01, kafka-test-02, rpc-test-01, rpc-test-02, platform-test-01, platform-test-02

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
