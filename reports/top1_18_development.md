# Development record for the 18/18 target

This is a new follow-up request after task2 completed with a genuine 16/18 result. Previous reports, scores and archived source remain unchanged. Replay numbers below are development checks, not new live acceptance results.

## Failure analysis

The 20261007T134537Z-17c43e Full failures both selected DB_SLOW_QUERY instead of RPC_ERROR_RATE. Payment errors had one slow database call among 49, at alert minus 181 seconds; dependency unavailability had one among 48. A single typed span received direct causal weight despite its age or prevalence. The true database incidents had 14–16 slow calls, often lasting 10–30 seconds, among 126–146 recent queries.

The first Normal window had four slow calls among 31 (1.1–2.2 seconds), with median 85ms. Fixed Normal repeat three asserted RPC_TIMEOUT from a 6.6x mean-latency ratio despite an observed zero timeout rate and zero error rate.

## Iteration 1

1. Group timestamped database calls by service, database system and operation, deduplicating trace/span IDs. Require incident locality (one minute before through one minute after the alert), at least three slow calls and group mean exceeding the same one-second latency budget used by the anomaly detector. Retain other slow calls as diagnostic hints. This does not assert that stable slowness is a new fault; a baseline-aware trace distribution is still a future extension. Rare-tail incidents may be missed by this conservative rule.
2. Apply the same cohort corroboration to live db.slowlog causal evidence, while preserving raw observed slow-query counts and rows for audit.
3. Do not infer timeout from latency growth when a timeout rate was observed below the timeout threshold. Preserve actual timeout and RPC error evidence. Existing snapshots with no timeout outcome retain the legacy fallback.

Planner, Fallback, Evidence Gate, Ranker and budgets remain unchanged. No scenario IDs, flags, ground truth, scorer or load changes.

Latest release replay: Full 16/18 -> 18/18; Fixed 16/18 -> 18/18; No-L2 13/18 -> 15/18. Normal: Full 2/3 -> 3/3; Fixed 1/3 -> 3/3; No-L2 2/3 -> 3/3. No-L2's missing Kafka domain measurements remain absent; replay does not synthesize tools.

Older October 5 observations, with the existing temporal-context rehydration: Full 15/18, Fixed 18/18, No-L2 15/18; Normal 3/3 throughout. The three Full misses retain missing Kafka domain measurements from the original execution. These are diagnostic limitations of a replay, not new live Planner outcomes.

Meaningful regression coverage includes old calls competing with current RPC errors, isolated latency tails, repeated current database slowness and observed zero versus actual timeout outcomes. Validation before freeze: 292 passed, 4 skipped; Ruff passed.

Development artifacts: `artifacts/otel_demo_rca_v1/top1_18_development/{before,iteration_01,older_release_check}.json`. The new live configuration is `benchmarks/datasets/otel_demo/v1/top1_18.yaml`, with unchanged official release timing, three repeats and all three paired modes. Only its completed recovered lifecycles establish the requested fresh accuracy.

## First fresh release and iteration 2

The complete first release `20261009T043206Z-54bfc1` produced Full 16/18, Fixed 17/18, No-L2 14/18; Normal 3/3 for all three. All 21 lifecycles recovered. Independent audit, 301 tests and Ruff passed. This result and its failures are retained in `top1_18_iteration1.md`; it is not relabeled as 18/18.

Full missed the first queue incident because it never requested lag. Its RPC probe actually measured 20 outgoing calls, zero errors, zero timeouts and mean latency below baseline. The planner could not see those normal results because only positive causal evidence reached its bounded context. Its bare expert names also did not describe the measurements they offer. A development probe adding normal-outcome context and capability descriptions redirected all three tested last-round decisions to the messaging measurement. A full cached workflow exposed an earlier problem: invoking experts before inspecting dependencies can waste both expert slots. The final small prompt clarification requires observed dependencies and asks for traces before expert guesses when only generic symptoms exist. This is the evidence-backed minimal Planner exception permitted by task2, not a deterministic domain router. Fallback, action authorization, Gate, Ranker, seed tools and budgets remain unchanged.

The first memory incident had a reference beginning `[0.782, 0.470, 0.471, 0.515, 0.515, 0.510]`. The recent median was about 0.572. The pre-restart point inflated MAD and hid the material growth. Fractional resource comparisons now start after the last large downward baseline discontinuity (over ten percentage points and twenty percent), require at least three subsequent reference samples and retain the existing delta/ratio/noise thresholds. The discontinuity is inferred from telemetry; no restart-control timestamps or scenario metadata enter the rule. Stable post-reset observations and insufficient new history do not vote for growth.

Iteration-2 historical replay of the first fresh run: Full 17/18, Fixed 18/18, No-L2 15/18; Normal 3/3 throughout. The Full queue miss remains because this replay does not invent its missing tool. The original October 7 replay remains Full/Fixed 18/18, Normal 3/3. A separately labeled development workflow using paired cached Fixed measurements and real Planner API calls requested lag in all three queue cases and selected it as Top1; this is not fresh accuracy. Actual requests and responses are retained in `cached_dependency_first_validation`. The earlier failed probe is retained in `cached_planner_validation`.

Validation before the second freeze: 304 passed, 4 skipped; Ruff passed. The new configuration `top1_18_v2.yaml` changes only the experiment version. The second full fresh run uses the same labels, scorer, load, model, lifecycle timing, budgets and rotating paired mode order. No rules change during that run.

## Faster development workflow

The user requested shorter experimentation while the second release was running. Future development should use regression plus historical replay first, then one smoke lifecycle for each affected scenario. Run the full paired release only after those checks pass and the rules are frozen. Replay and smoke must not be relabeled as the official 18-fault acceptance result.

```powershell
python -m pytest -q tests/regression/test_accuracy_closure.py
python -m opspilot.otel_benchmark.runner --profile smoke --repeat 1 --scenario messaging-backlog
```

Smoke has 30s warmup, 45s stabilization, 30s observation and 45s cooldown: 150s fixed waiting per scenario, plus health checks, diagnosis and actual recovery. It may expose insufficient time for a slow-growing fault; lack of a measured symptom is not success. Release retains 90/180/60/90 seconds, three repeats and paired modes: fixed waiting alone is 147 minutes for 21 lifecycles. A running controller holds an exclusive lock; do not overlap smoke and release injections. Reducing mode count mostly saves diagnosis seconds rather than the dominant lifecycle waits.
