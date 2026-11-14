---
name: rpc-timeout-triage
description: Investigate rpc timeout rate, error rate and latency after a failed or abnormally slow downstream call.
---

# RPC timeout triage

Use only for a downstream target observed in an incident-local call chain.

## Tool order
- `rpc.metrics`

## Required evidence and stop condition
Check timeout and error rates with call volume, then latency against a comparable baseline. A failed span alone does not establish a rate anomaly. Report scope, window, source ID and any missing measurement; final diagnosis remains deterministic.
