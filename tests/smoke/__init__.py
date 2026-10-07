"""Business snapshots with observable L1 clues for deterministic fallback tests."""


def dependency_trace(service):
    return {"traces": [{"trace_id": "smoke", "spans": [
        {"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 10},
        {"span_id": "dependency", "parent_span_id": "root", "service": service,
         "status": "TIMEOUT" if service == "payment" else "SLOW", "duration_ms": 1500},
    ]}]}
