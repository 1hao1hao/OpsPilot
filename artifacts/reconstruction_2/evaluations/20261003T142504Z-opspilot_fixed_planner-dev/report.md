# Evaluation Report

- Evaluation: `20261003T142504Z-opspilot_fixed_planner-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_fixed_planner`

## Metrics

- Root Cause Hit@1: 1.000 (21/21)
- Root Cause Hit@3: 1.000 (21/21)
- Evidence Recall (macro): 1.0
- Tool Success Rate: 1.000 (325/325)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 0.871 ms
- Average Tool / Expert calls: 13.0 / 4.0
- Average Investigation rounds: 1.0
- Planner Action Valid Rate: 1.000 (425/425)
- Duplicate Action Rate: 0.000 (0/425)
- Budget Exhaustion Rate: 0.000 (0/25)
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

none

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
