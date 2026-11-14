---
name: redis-cache-triage
description: Investigate redis cache memory pressure, evictions and hit-rate degradation for an observed cache dependency.
---

# Redis cache triage

Use only when the alert or trace identifies a cache dependency in scope.

## Tool order
- `redis.memory`
- `redis.hotkeys`

## Required evidence and stop condition
Measure eviction or memory pressure first, then hit rate or hot keys if needed. Normal measurements weaken the cache hypothesis. Missing baselines or failed queries remain unresolved. Return scoped observations and source IDs without deciding the final verdict.
