# Evaluation Report

- Evaluation: `20260816T083744Z-deepseek_hybrid-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `deepseek_hybrid`

## Metrics

- Root Cause Hit@1: 1.000 (10/10)
- Root Cause Hit@3: 1.000 (10/10)
- Evidence Recall (macro): 1.0
- Tool Success Rate: 1.000 (90/90)
- E2E Success Rate: 0.833 (10/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 2194.678 ms
- Model API calls: 10
- Prompt / completion / total tokens: 4080 / 783 / 4863

## Failures

normal-test-01, normal-test-02

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
