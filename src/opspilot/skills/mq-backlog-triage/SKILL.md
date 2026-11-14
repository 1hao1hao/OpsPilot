---
name: mq-backlog-triage
description: Investigate kafka and MQ consumer backlog in an observed asynchronous message chain or completion-delay alert.
---

# MQ backlog triage

Use when producer/consumer evidence links the message chain to the alert. Generic service topology alone is insufficient.

## Tool order
- `kafka.lag`

## Required evidence and stop condition
Check consumer lag and production/consumption rates for the target and incident window. A normal request latency does not prove asynchronous completion. Report no data, conflicting windows or normal lag as unresolved or weakening evidence. Do not infer the final root cause.
