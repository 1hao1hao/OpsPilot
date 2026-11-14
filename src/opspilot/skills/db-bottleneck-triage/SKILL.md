---
name: db-bottleneck-triage
description: Investigate db query latency, connection pressure and replication lag after an observed database dependency.
---

# DB bottleneck triage

Use only for a database dependency observed in the alert scope. Confirm the target service and time window before reading metrics. A slow span is a lead, not proof of a database bottleneck.

## Tool order
- `db.slowlog`
- `db.connections`
- `db.replication`

## Required evidence and stop condition
Compare direct query latency or slow-query count with a healthy reference; check connection pressure when slow queries do not explain the symptom. Stop when direct measurements are normal, missing, or conflicting and report the unresolved requirement. Return scoped observations and source IDs; never assert a root cause from the skill text.
