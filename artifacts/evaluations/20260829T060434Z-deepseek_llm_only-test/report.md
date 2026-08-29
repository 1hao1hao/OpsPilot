# Evaluation Report

- Evaluation: `20260829T060434Z-deepseek_llm_only-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `deepseek_llm_only`

## Metrics

- Root Cause Hit@1: 0.400 (4/10)
- Root Cause Hit@3: 0.400 (4/10)
- Evidence Recall (macro): 0.0
- Tool Success Rate: n/a
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 1949.359 ms
- Model API calls: 12
- Prompt / completion / total tokens: 4194 / 833 / 5027

## Failures

db-test-01, db-test-02, redis-test-02, kafka-test-01, kafka-test-02, platform-test-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
