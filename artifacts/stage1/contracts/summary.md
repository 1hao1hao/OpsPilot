# Stage 1 Contract and Benchmark Evidence

Run date: 2026-08-12 (Asia/Shanghai workspace; artifacts use UTC IDs).

## Scope actually completed

- Added strict, versioned Alert/Plan/Tool/Evidence/RootCause/Report/EvaluationCase contracts.
- Added a read-only Tool Registry and one Executor boundary for validation, timeout, normalized errors and execution records.
- Added Coordinator and Root Cause as the only OpsPilot Agents; DB/Redis/Kafka/RPC are registered Tools.
- Added deterministic evidence extraction and stable `root_cause_type` scoring with a fake summary model.
- Routed legacy `POST /api/v1/analyze` through the OpsPilot workflow adapter.
- Added the `opspilot-rca@1.0.0` dataset: 36 cases, dev 24, frozen test 12, six balanced categories.
- Added system-generated predictions, metrics, failures and Markdown report artifacts.

PostgreSQL, Redis Queue, Worker and Checkpoint were not implemented; they belong to Stage 2.

## Installation and compatibility baseline

Command:

```bash
python -m pip install -e '.[dev]'
```

Result: passed. Dependency bounds were corrected to keep LangChain 0.2 compatible with `langchain-openai<0.2`, and to avoid upgrading the shared environment to pandas 3 / packaging 24.

The first legacy unit run produced 166 passes and one LangGraph 0.1.19 compatibility failure because node `root_cause` collided with state key `root_cause`. Renaming only the internal graph node to `root_cause_agent` retained the state/API contract. The final combined test result is below.

The shared Anaconda environment still has four unrelated `pip check` findings from preinstalled conda-repo-cli/gensim/anaconda-cloud-auth packages. OpsPilot does not import or modify those packages.

## Test and lint evidence

```bash
ruff check src tests
# All checks passed

pytest -q tests/unit tests/contract tests/evaluation tests/regression
# 182 passed, 1 Starlette TestClient deprecation warning in 2.32s
```

`src/deeprca`, `tests/unit` and `tests/smoke` are lint-frozen during migration. A diagnostic full legacy scan before the exclusion reported 175 pre-existing findings; they were not bulk-rewritten as part of Stage 1. All new OpsPilot and Stage-1 test code is checked by the standard command.

## Dataset

- Name/version: `opspilot-rca@1.0.0`
- Schema: `1.0`
- Dev: 24 cases (four per category)
- Frozen test: 12 cases (two per category)
- Categories: DB, Redis, Kafka, RPC, Deploy/Resource, Normal/Noise
- Normal/Noise: six total cases
- Labels: stable root-cause and evidence types; free text is not scored
- Frozen test was run after dev results were locked; no algorithm or label was changed afterward.

## Same-set results

The `deeprca_baseline` configuration is an explicitly controlled alert-text-only baseline. It does not call Tools or an external LLM. It is not presented as a production measurement of the complete legacy HTTP graph.

| Split | System | Hit@1 | Hit@3 | Evidence Recall | Tool Success | E2E | Normal FPR | P95 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| dev (20 fault + 4 normal) | alert-text baseline | 6/20 (0.30) | 6/20 (0.30) | 0.00 | n/a (0 calls) | 24/24 | 0/4 | 0.006 |
| dev | OpsPilot hybrid | 20/20 (1.00) | 20/20 (1.00) | 1.00 | 216/216 | 24/24 | 0/4 | 0.406 |
| frozen test (10 fault + 2 normal) | alert-text baseline | 1/10 (0.10) | 1/10 (0.10) | 0.00 | n/a (0 calls) | 12/12 | 0/2 | 0.018 |
| frozen test | OpsPilot hybrid | 10/10 (1.00) | 10/10 (1.00) | 1.00 | 108/108 | 12/12 | 0/2 | 0.843 |

These are deterministic synthetic-snapshot results, not production accuracy claims. Stage 1 has no queue time, real LLM latency or runtime recovery latency.

## Evaluation artifacts

- `artifacts/evaluations/20260812T040942Z-deeprca_baseline-dev/`
- `artifacts/evaluations/20260812T040946Z-opspilot_hybrid-dev/`
- `artifacts/evaluations/20260812T041314Z-deeprca_baseline-test/`
- `artifacts/evaluations/20260812T041314Z-opspilot_hybrid-test/`

Every directory contains `manifest.json`, `predictions.jsonl`, `metrics.json`, `failures.jsonl` and `report.md`. Prediction counts equal split case counts; baseline failures were retained. `db-dev-02`, a generic alert-text miss by the baseline, is protected by an executable regression proving that the Hybrid system uses DB Tool evidence.
