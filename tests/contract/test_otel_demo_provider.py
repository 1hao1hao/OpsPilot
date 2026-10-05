import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from opspilot.config import RuntimeSettings
from opspilot.evidence import collect_evidence
from opspilot.models import AlertEvent, ToolCall, ToolStatus
from opspilot.observations.clients import BackendError, JaegerClient
from opspilot.observations.provider import OpenTelemetryDemoProvider
from opspilot.tools.executor import ToolExecutor
from opspilot.tools.registry import build_default_registry
from opspilot.tracing import detect_span_anomalies

FIXTURES = Path(__file__).parents[1] / "fixtures/otel_demo"


async def test_jaeger_v3_search_preserves_otlp_span_metadata():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/api/traces"):
            return httpx.Response(404, text="404 page not found")
        assert request.url.path.endswith("/api/v3/traces")
        assert request.url.params["query.serviceName"] == "frontend"
        assert request.url.params["query.durationMin"] == "1000ms"
        return httpx.Response(200, json={"result": {"resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "frontend"}}]},
            "scopeSpans": [{"spans": [{
                "traceId": "trace-1", "spanId": "child", "parentSpanId": "root", "name": "request",
                "startTimeUnixNano": "1000000000", "endTimeUnixNano": "3000000000", "kind": 3,
                "status": {"code": 2},
                "attributes": [{"key": "rpc.response.status_code", "value": {"intValue": "4"}}],
            }]}],
        }]}})

    client = JaegerClient("http://jaeger/jaeger/ui", transport=httpx.MockTransport(handler))
    result = await client.traces("frontend", 0, 10, min_duration_ms=1000)
    span = result["traces"][0]["spans"][0]
    assert span["parent_span_id"] == "root"
    assert span["start_time"] == 1
    assert span["duration_ms"] == 2000
    assert span["span_kind"] == "client"
    assert span["is_timeout"] is True
    assert span["status"] == "TIMEOUT"
    assert len(seen) == 2


@pytest.mark.parametrize("response,empty", [
    (httpx.Response(404, json={"error": {"httpCode": 404, "message": "No traces found"}}), True),
    (httpx.Response(404, text="404 page not found"), False),
    (httpx.Response(500, json={"error": {"message": "storage unavailable"}}), False),
    (httpx.Response(200, json={"result": {"resourceSpans": "invalid"}}), False),
])
async def test_jaeger_v3_only_documented_no_results_is_empty(response, empty):
    def handler(request):
        return httpx.Response(404, text="404 page not found") if request.url.path.endswith("/api/traces") else response

    client = JaegerClient("http://jaeger", transport=httpx.MockTransport(handler))
    if empty:
        assert await client.traces("frontend", 0, 10) == {"traces": []}
    else:
        with pytest.raises(BackendError):
            await client.traces("frontend", 0, 10)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def settings(**kwargs):
    return RuntimeSettings(
        _env_file=None,
        observation_backend="otel_demo",
        prometheus_url="http://prom",
        jaeger_url="http://jaeger/jaeger/ui",
        opensearch_url="http://logs",
        **kwargs,
    )


def alert():
    return AlertEvent(
        alert_id="live-test",
        service_name="frontend",
        alert_type="error_rate",
        severity="P1",
        timestamp=datetime(2026, 10, 3, 12, tzinfo=UTC),
        description="checkout error rate elevated",
    )


def provider(handler):
    return OpenTelemetryDemoProvider(settings(), transport=httpx.MockTransport(handler))


async def test_cpu_counter_rate_preferred_over_docker_lifetime_gauge():
    seen = []

    def handler(request):
        if request.url.path.endswith("/label/__name__/values"):
            return httpx.Response(200, json={"status": "success", "data": ["container_cpu_usage_nanoseconds_total", "container_cpu_utilization_ratio"]})
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"status": "success", "data": ["container_name"]})
        if request.url.path.endswith("/series"):
            return httpx.Response(200, json={"status": "success", "data": [
                {"__name__": "container_cpu_usage_nanoseconds_total", "container_name": "frontend"},
                {"__name__": "container_cpu_utilization_ratio", "container_name": "frontend"},
            ]})
        query = request.url.params["query"]
        seen.append(query)
        assert 'rate(container_cpu_usage_nanoseconds_total{container_name="frontend"}[1m])' in query
        assert "/ 1000000000" in query
        assert "container_cpu_utilization_ratio" not in query
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [{"values": [[alert().timestamp.timestamp(), "0.002"]]}]}})

    result = await execute(provider(handler), "metrics.query")
    assert result.status == ToolStatus.SUCCESS
    assert result.data["observations"]["cpu_usage"]["current"] == 0.002
    assert len(seen) == 1


async def execute(p, tool):
    return await ToolExecutor(build_default_registry(provider=p)).execute(
        ToolCall(tool_call_id=tool, tool_name=tool, arguments={"alert": alert().model_dump(mode="json")})
    )


async def test_jaeger_adapts_ids_parents_units_and_produces_evidence():
    def handler(request):
        assert request.url.path == "/jaeger/ui/api/traces"
        assert int(request.url.params["start"]) == int((alert().timestamp.timestamp() - 900) * 1e6)
        return httpx.Response(200, json=fixture("jaeger"))

    result = await execute(provider(handler), "traces.query")
    assert result.status == ToolStatus.SUCCESS
    observations = result.data["observations"]
    child = observations["traces"][0]["spans"][1]
    assert child["duration_ms"] == 2100
    assert child["parent_span_id"] == "root"
    assert detect_span_anomalies(observations)[0].path == ("frontend", "payment")
    assert any(e.service == "payment" for e in collect_evidence(alert(), [result]))


async def test_log_query_is_scoped_prioritized_and_bounded():
    def handler(request):
        query = json.loads(request.content)
        assert query["size"] == 50
        assert query["query"]["bool"]["filter"][0] == {"term": {"resource.service.name.keyword": "frontend"}}
        assert "observedTimestamp" in query["query"]["bool"]["filter"][1]["range"]
        assert query["query"]["bool"]["should"]
        return httpx.Response(200, json=fixture("opensearch"))

    result = await execute(provider(handler), "logs.query")
    assert result.data["observations"]["logs"][0]["severity"] == "ERROR"
    assert collect_evidence(alert(), [result])[0].evidence_type == "log.oom"


async def test_metrics_discovery_scoping_window_and_missing_series():
    queries = []

    def handler(request):
        path = request.url.path
        if path.endswith("/label/__name__/values"):
            data = ["container_cpu_utilization", "container_memory_percent"]
        elif path.endswith("/labels"):
            data = ["container_name", "service_name"]
        elif path.endswith("/series"):
            data = [
                {"__name__": "container_cpu_utilization", "container_name": "frontend"},
                {"__name__": "container_memory_percent", "container_name": "another-service"},
            ]
        else:
            queries.append(request.url.params["query"])
            assert float(request.url.params["end"]) == alert().timestamp.timestamp() + 60
            return httpx.Response(200, json=fixture("prometheus"))
        return httpx.Response(200, json={"status": "success", "data": data})

    result = await execute(provider(handler), "metrics.query")
    obs = result.data["observations"]
    assert set(obs) == {"cpu_usage"}
    assert queries == ['clamp_max(max(container_cpu_utilization{container_name="frontend"}) / 100, 1)']
    assert len(obs["cpu_usage"]["data_points"]) == 2
    assert obs["cpu_usage"]["baseline_series"] == [0.2]
    assert collect_evidence(alert(), [result])


@pytest.mark.parametrize(
    "tool,code",
    [
        ("metrics.query", "prometheus_unavailable"),
        ("traces.query", "jaeger_unavailable"),
        ("logs.query", "opensearch_unavailable"),
        ("db.connections", "prometheus_unavailable"),
        ("db.slowlog", "jaeger_unavailable"),
        ("redis.memory", "prometheus_unavailable"),
        ("kafka.lag", "prometheus_unavailable"),
    ],
)
async def test_backend_failure_is_error_not_empty_success(tool, code):
    result = await execute(provider(lambda _: httpx.Response(503)), tool)
    assert result.status == ToolStatus.ERROR
    assert result.error_code == code
    assert not collect_evidence(alert(), [result])


async def test_no_ground_truth_or_mock_fallback_and_replica_gap():
    def forbidden(_):
        raise AssertionError("unsupported tools must not query flagd or mock HTTP")

    p = provider(forbidden)
    a = alert().model_copy(
        update={"signals": {"change": {"recent_deployment": True}}, "labels": {"fault": "paymentFailure"}}
    )
    assert await p.read("change", a) == {}
    assert await p.read_tool("db.replication", a) == {}


async def test_empty_backend_success_is_empty_observation():
    p = provider(lambda _: httpx.Response(200, json={"data": [], "errors": None}))
    assert await p.read("trace", alert()) == {"traces": []}


async def test_trace_filters_sent_to_jaeger():
    def handler(request):
        assert json.loads(request.url.params["tags"]) == {"error": "true"}
        assert request.url.params["minDuration"] == "1000ms"
        return httpx.Response(200, json={"data": []})

    await provider(handler).jaeger.traces("frontend", 1, 2, error=True, min_duration_ms=1000)


async def test_partial_logs_and_prometheus_errors_rejected():
    p = provider(lambda _: httpx.Response(200, json={"timed_out": True, "hits": {"hits": []}}))
    with pytest.raises(BackendError):
        await p.read("log", alert())
    p = provider(lambda _: httpx.Response(200, json={"status": "error", "error": "query failed"}))
    with pytest.raises(BackendError):
        await p.read("metric", alert())


def test_backend_selection_requires_urls_and_defaults_to_mock():
    assert RuntimeSettings(_env_file=None).observation_backend == "mock"
    with pytest.raises(ValueError):
        RuntimeSettings(_env_file=None, observation_backend="otel_demo")
    from opspilot.runtime.faults import build_worker_registry

    registry = build_worker_registry(settings())
    assert registry.names() == build_default_registry().names()


@pytest.mark.parametrize(
    "tool,names,value,expected",
    [
        ("kafka.lag", ["kafka_consumer_group_lag_sum"], 20000, "kafka.consumer_lag"),
        ("redis.memory", ["redis_memory_used_bytes", "redis_maxmemory_bytes"], 95, "redis.memory_usage"),
        ("redis.hotkeys", ["redis_keyspace_hits_total", "redis_keyspace_misses_total"], 60, "redis.hit_rate"),
    ],
)
async def test_domain_metric_mapping_and_evidence(tool, names, value, expected):
    def handler(request):
        path = request.url.path
        if path.endswith("/label/__name__/values"):
            data = names
        elif path.endswith("/labels"):
            data = []
        elif path.endswith("/series"):
            data = [{"__name__": name} for name in names]
        else:
            data = {"resultType": "matrix", "result": [{"metric": {}, "values": [[1791028800, str(value)]]}]}
        return httpx.Response(200, json={"status": "success", "data": data})

    result = await execute(provider(handler), tool)
    assert result.status == ToolStatus.SUCCESS
    assert any(e.evidence_type == expected for e in collect_evidence(alert(), [result]))


async def test_db_slow_spans_are_observed_counts_and_connections_not_invented():
    data = fixture("jaeger")
    data["data"][0]["spans"][1]["tags"].append({"key": "db.system", "value": "postgresql"})
    result = await execute(provider(lambda _: httpx.Response(200, json=data)), "db.slowlog")
    assert result.data["observations"]["slow_query_count"] == 1
    assert result.data["observations"]["slow_queries"][0]["span_id"] == "child"

    def handler(request):
        if request.url.path.endswith("/label/__name__/values"):
            data = ["postgresql_backends", "postgresql_connection_max_connections"]
        elif request.url.path.endswith("/labels"):
            data = []
        elif request.url.path.endswith("/series"):
            data = [{"__name__": "postgresql_backends"}, {"__name__": "postgresql_connection_max_connections"}]
        else:
            assert "postgresql_backends" not in request.url.params["query"]
            data = {"resultType": "matrix", "result": [{"metric": {}, "values": [[1791028800, "100"]]}]}
        return httpx.Response(200, json={"status": "success", "data": data})

    result = await execute(provider(handler), "db.connections")
    assert result.data["observations"] == {"max_connections": 100}
    assert collect_evidence(alert(), [result]) == []


async def test_missing_metrics_are_not_queried_or_fabricated():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"status": "success", "data": []})

    result = await execute(provider(handler), "metrics.query")
    assert result.status == ToolStatus.SUCCESS
    assert result.data["observations"] == {}
    assert not any("query_range" in path for path in paths)


async def test_redis_used_bytes_never_treated_as_percentage():
    def handler(request):
        path = request.url.path
        if path.endswith("/label/__name__/values"):
            data = ["redis_memory_used_bytes"]
        elif path.endswith("/labels"):
            data = []
        elif path.endswith("/series"):
            data = [{"__name__": "redis_memory_used_bytes"}]
        else:
            data = {"resultType": "matrix", "result": [{"metric": {}, "values": [[1791028800, "900000"]]}]}
        return httpx.Response(200, json={"status": "success", "data": data})

    result = await execute(provider(handler), "redis.memory")
    assert result.data["observations"] == {"used_memory": 900000}
    assert collect_evidence(alert(), [result]) == []


async def test_backend_outage_reaches_existing_degraded_report():
    from opspilot.graph.workflow import OpsPilotWorkflow

    p = provider(lambda _: httpx.Response(503))
    report = await OpsPilotWorkflow(build_default_registry(provider=p), settings=settings()).analyze(alert())
    assert report.degraded
    assert "metrics.query" in report.missing_sources
    assert "logs.query" in report.missing_sources
    assert report.evidence == []


def test_live_timeout_defaults_preserve_explicit_limits():
    from opspilot.runtime.faults import build_worker_registry

    assert settings().tool_timeout_seconds == 30
    assert build_worker_registry(settings()).get("metrics.query").timeout_seconds == 30
    assert build_default_registry(settings=settings()).get("metrics.query").timeout_seconds == 30
    assert settings(tool_timeout_seconds=0.5).tool_timeout_seconds == 0.5
    assert build_default_registry(timeout_seconds=0.1, settings=settings()).get("metrics.query").timeout_seconds == 0.1
    assert RuntimeSettings(_env_file=None).tool_timeout_seconds == 0.2
    assert build_default_registry().get("metrics.query").timeout_seconds == 2


async def test_rpc_excludes_internal_spans_and_separates_overlapping_trace_windows():
    timestamp = alert().timestamp.timestamp()
    # Both Jaeger searches return the same tree. Baseline must not reuse current calls.
    raw = fixture("jaeger")
    trace = raw["data"][0]
    trace["spans"] = []
    for span_id, offset, kind, duration, status in [
        ("old", -60, "client", 100_000, "OK"),
        ("timeout", 1, "client", 5_000_000, "DEADLINE_EXCEEDED"),
        ("success", 2, "client", 100_000, "OK"),
        ("internal", 3, "internal", 30_000_000, "DEADLINE_EXCEEDED"),
        ("future", 120, "client", 30_000_000, "DEADLINE_EXCEEDED"),
    ]:
        trace["spans"].append(
            {
                "spanID": span_id,
                "processID": "p1",
                "operationName": "request",
                "duration": duration,
                "startTime": int((timestamp + offset) * 1e6),
                "tags": [{"key": "span.kind", "value": kind}, {"key": "rpc.response.status_code", "value": status}],
                "references": [],
            }
        )
    result = await execute(provider(lambda _: httpx.Response(200, json=raw)), "rpc.metrics")
    assert result.status == ToolStatus.SUCCESS
    assert result.data["observations"] == {
        "error_rate": 0.5,
        "timeout_rate": 0.5,
        "call_volume": 2,
        "latency_ms": 2550,
        "baseline_latency_ms": 100,
    }
    assert any(e.evidence_type == "rpc.timeout" for e in collect_evidence(alert(), [result]))


async def test_rpc_does_not_invent_timeout_or_baseline():
    raw = fixture("jaeger")
    span = raw["data"][0]["spans"][0]
    span.update(
        startTime=int((alert().timestamp.timestamp() + 1) * 1e6),
        tags=[{"key": "span.kind", "value": "client"}, {"key": "error", "value": True}],
    )
    result = await execute(provider(lambda _: httpx.Response(200, json=raw)), "rpc.metrics")
    assert result.data["observations"] == {"error_rate": 1, "call_volume": 1}


async def test_prometheus_dotted_labels_are_quoted_and_match_one_real_series():
    queries = []

    def handler(request):
        path = request.url.path
        if path.endswith("/label/__name__/values"):
            data = ["container_cpu_utilization", "traces_span_metrics_calls_total"]
        elif path.endswith("/labels"):
            data = ["service.name"]
        elif path.endswith("/series"):
            assert '{"service.name"="frontend"}' in request.url.params.get_list("match[]")
            data = [
                {"__name__": "container_cpu_utilization", "service.name": "frontend"},
                {
                    "__name__": "traces_span_metrics_calls_total",
                    "service.name": "frontend",
                    "span_kind": "SPAN_KIND_CLIENT",
                },
                {
                    "__name__": "traces_span_metrics_calls_total",
                    "service.name": "another",
                    "span_kind": "SPAN_KIND_SERVER",
                },
            ]
        else:
            queries.append(request.url.params["query"])
            return httpx.Response(200, json=fixture("prometheus"))
        return httpx.Response(200, json={"status": "success", "data": data})

    result = await execute(provider(handler), "metrics.query")
    assert set(result.data["observations"]) == {"cpu_usage"}
    assert queries == ['clamp_max(max(container_cpu_utilization{"service.name"="frontend"}) / 100, 1)']


@pytest.mark.parametrize(
    "tool,payload,code",
    [
        ("traces.query", {"data": [{"traceID": "broken", "spans": [{"spanID": "s"}]}]}, "jaeger_unavailable"),
        ("logs.query", {"hits": {"hits": [{"_source": {"body": "missing timestamp"}}]}}, "opensearch_unavailable"),
        ("metrics.query", {"status": "success", "data": None}, "prometheus_unavailable"),
    ],
)
async def test_malformed_backend_records_are_not_empty_success(tool, payload, code):
    result = await execute(provider(lambda _: httpx.Response(200, json=payload)), tool)
    assert result.status == ToolStatus.ERROR
    assert result.error_code == code


async def test_all_l1_metric_queries_use_discovered_names_and_keep_history():
    names = [
        "traces_span_metrics_calls_total",
        "traces_span_metrics_duration_milliseconds_bucket",
        "container_cpu_utilization",
        "container_memory_percent",
    ]
    queries = []

    def handler(request):
        path = request.url.path
        if path.endswith("/label/__name__/values"):
            data = names
        elif path.endswith("/labels"):
            data = ["service_name", "span_kind", "status_code"]
        elif path.endswith("/series"):
            data = [
                {
                    "__name__": name,
                    "service_name": "frontend",
                    "span_kind": "SPAN_KIND_SERVER",
                    "status_code": "STATUS_CODE_ERROR",
                }
                for name in names
            ]
        else:
            queries.append(request.url.params["query"])
            return httpx.Response(200, json=fixture("prometheus"))
        return httpx.Response(200, json={"status": "success", "data": data})

    result = await execute(provider(handler), "metrics.query")
    assert result.status == ToolStatus.SUCCESS
    observations = result.data["observations"]
    assert set(observations) == {"qps", "error_rate", "tp95", "tp99", "cpu_usage", "memory_usage"}
    assert all(value["baseline_series"] == [0.2] for value in observations.values())
    assert any("histogram_quantile(0.99" in q for q in queries)
    assert any(q.startswith("100 * sum(rate(") and 'status_code="STATUS_CODE_ERROR"' in q for q in queries)
    from opspilot.rca.anomaly import AnomalyDetector

    assert AnomalyDetector().detect(alert(), [result])
