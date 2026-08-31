# Evaluation Report

- Evaluation: `20260831T081532Z-opspilot_full_adaptive-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `opspilot_full_adaptive`

## Metrics

- Root Cause Hit@1: 1.000 (10/10)
- Root Cause Hit@3: 1.000 (10/10)
- Evidence Recall (macro): 1.0
- Tool Success Rate: 1.000 (58/58)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 1.954 ms
- Average Tool / Expert calls: 4.833333 / 2.0
- Average Investigation rounds: 3.083333
- Planner Action Valid Rate: 1.000 (82/82)
- Duplicate Action Rate: 0.000 (0/82)
- Budget Exhaustion Rate: 1.000 (12/12)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

none

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
