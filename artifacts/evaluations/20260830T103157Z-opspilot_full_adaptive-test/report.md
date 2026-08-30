# Evaluation Report

- Evaluation: `20260830T103157Z-opspilot_full_adaptive-test`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `test` / 12
- System: `opspilot_full_adaptive`

## Metrics

- Root Cause Hit@1: 1.000 (10/10)
- Root Cause Hit@3: 1.000 (10/10)
- Evidence Recall (macro): 1.0
- Tool Success Rate: 1.000 (51/51)
- E2E Success Rate: 1.000 (12/12)
- False Positive Rate: 0.000 (0/2)
- P95 latency: 1.415 ms
- Average Tool / Expert calls: 4.25 / 0.916667
- Average Investigation rounds: 2.333333
- Planner Action Valid Rate: 1.000 (74/74)
- Duplicate Action Rate: 0.000 (0/74)
- Budget Exhaustion Rate: 0.167 (2/12)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

none

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
