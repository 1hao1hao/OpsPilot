# Evaluation Report

- Evaluation: `20260831T081531Z-opspilot_full_adaptive-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_full_adaptive`

## Metrics

- Root Cause Hit@1: 1.000 (21/21)
- Root Cause Hit@3: 1.000 (21/21)
- Evidence Recall (macro): 1.0
- Tool Success Rate: 1.000 (124/124)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 0.966 ms
- Average Tool / Expert calls: 4.96 / 1.96
- Average Investigation rounds: 3.0
- Planner Action Valid Rate: 1.000 (173/173)
- Duplicate Action Rate: 0.000 (0/173)
- Budget Exhaustion Rate: 0.960 (24/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

none

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
