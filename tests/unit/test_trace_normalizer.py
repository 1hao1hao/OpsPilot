from opspilot.tracing import detect_span_anomalies, normalize_trace_payload


def test_native_span_tree_retains_complete_failure_path():
    payload = {
        "traces": [{
            "trace_id": "t1",
            "spans": [
                {"span_id": "s1", "service": "order-service", "status": "OK"},
                {"span_id": "s2", "parent_span_id": "s1", "service": "payment-service", "status": "OK"},
                {"span_id": "s3", "parent_span_id": "s2", "service": "redis", "duration_ms": 1200, "status": "ERROR"},
            ],
        }]
    }
    anomaly = detect_span_anomalies(payload)[0]
    assert anomaly.path == ("order-service", "payment-service", "redis")
    assert anomaly.service == "redis"
    assert anomaly.is_error and anomaly.is_slow


def test_jaeger_adapter_does_not_require_external_format_changes():
    payload = {
        "data": [{
            "traceID": "jaeger-1",
            "processes": {"p1": {"serviceName": "order-service"}, "p2": {"serviceName": "redis"}},
            "spans": [
                {"traceID": "jaeger-1", "spanID": "root", "processID": "p1", "operationName": "request", "duration": 20000, "references": [], "tags": []},
                {"traceID": "jaeger-1", "spanID": "child", "processID": "p2", "operationName": "GET", "duration": 1500000, "references": [{"refType": "CHILD_OF", "spanID": "root"}], "tags": [{"key": "error", "value": True}]},
            ],
        }]
    }
    traces = normalize_trace_payload(payload)
    anomaly = detect_span_anomalies(payload)[0]
    assert traces[0].spans[1].duration_ms == 1500
    assert anomaly.path == ("order-service", "redis")


def test_otel_adapter_reads_resource_service_and_parent_ids():
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "order-service"}}]},
                "scopeSpans": [{"spans": [{"traceId": "otel-1", "spanId": "root", "name": "request", "startTimeUnixNano": "0", "endTimeUnixNano": "10000000", "status": {"code": 1}}]}],
            },
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "redis"}}]},
                "scopeSpans": [{"spans": [{"traceId": "otel-1", "spanId": "child", "parentSpanId": "root", "name": "GET", "startTimeUnixNano": "0", "endTimeUnixNano": "1200000000", "status": {"code": 2}}]}],
            },
        ]
    }
    anomaly = detect_span_anomalies(payload)[0]
    assert anomaly.path == ("order-service", "redis")
    assert anomaly.status == "ERROR"
