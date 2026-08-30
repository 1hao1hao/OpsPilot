# Evaluation Report

- Evaluation: `20260830T102440Z-opspilot_adaptive_planner-dev`
- Dataset: `opspilot-rca@1.0.0`
- Split/cases: `dev` / 25
- System: `opspilot_adaptive_planner`

## Metrics

- Root Cause Hit@1: 0.952 (20/21)
- Root Cause Hit@3: 1.000 (21/21)
- Evidence Recall (macro): 1.0
- Tool Success Rate: 1.000 (131/131)
- E2E Success Rate: 1.000 (25/25)
- False Positive Rate: 0.000 (0/4)
- P95 latency: 0.965 ms
- Model API calls: 0
- Prompt / completion / total tokens: 0 / 0 / 0

## Failures

adaptive-db-dev-01

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
