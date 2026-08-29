# Evaluation Report

- Evaluation: `20260829T060459Z-deepseek_tools-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `deepseek_tools`

## Metrics

- Root Cause Hit@1: 0.900 (9/10)
- Root Cause Hit@3: 1.000 (10/10)
- Evidence Recall (macro): 0.0
- Tool Success Rate: 1.000 (120/120)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.500 (1/2)
- P95 latency: 2344.854 ms
- Model API calls: 12
- Prompt / completion / total tokens: 5137 / 1028 / 6165

## Failures

redis-test-02, normal-test-02

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
