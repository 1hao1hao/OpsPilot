"""Astronomy Shop observations; no flagd or benchmark metadata access."""

import asyncio
import json
import math
from datetime import UTC, datetime
from statistics import mean

from opspilot.config import RuntimeSettings
from opspilot.evidence.span_cohorts import database_latency_cohorts
from opspilot.evidence.temporal import temporal_payload
from opspilot.models import AlertEvent
from opspilot.observations.clients import JaegerClient, OpenSearchClient, PrometheusClient, label_name


class OpenTelemetryDemoProvider:
    def __init__(self, settings: RuntimeSettings, *, transport=None):
        self.settings = settings
        kw = {"timeout": settings.telemetry_timeout_seconds, "transport": transport}
        self.prometheus = PrometheusClient(settings.prometheus_url, **kw)
        self.jaeger = JaegerClient(settings.jaeger_url, **kw)
        self.opensearch = OpenSearchClient(settings.opensearch_url, **kw)

    def window(self, alert):
        end = alert.timestamp.timestamp() + self.settings.telemetry_window_after_seconds
        start = alert.timestamp.timestamp() - self.settings.telemetry_window_before_seconds
        return start, end

    async def read(self, signal_key: str, alert: AlertEvent) -> dict:
        start, end = self.window(alert)
        if signal_key in {"change", "problem", "topology"}:
            return {}  # No deployment/event inventory. Never inspect feature flags.
        if signal_key == "log":
            return await self.opensearch.logs(
                self.settings.opensearch_index,
                alert.service_name,
                datetime.fromtimestamp(start, UTC).isoformat(),
                datetime.fromtimestamp(end, UTC).isoformat(),
                self.settings.telemetry_log_limit,
            )
        if signal_key == "trace":
            return await self.jaeger.traces(alert.service_name, start, end, limit=self.settings.telemetry_trace_limit)
        if signal_key == "rpc":
            current, history = await asyncio.gather(
                self.jaeger.traces(
                    alert.service_name, alert.timestamp.timestamp(), end, limit=self.settings.telemetry_trace_limit
                ),
                self.jaeger.traces(
                    alert.service_name,
                    start,
                    alert.timestamp.timestamp() - 0.000001,
                    limit=self.settings.telemetry_trace_limit,
                ),
            )

            def spans(data, lower, upper):
                # Jaeger returns whole trees, including spans outside its search interval.
                # Count only outgoing calls of the requested service, once per span ID.
                unique = {
                    (s["trace_id"], s["span_id"]): s
                    for t in data["traces"]
                    for s in t["spans"]
                    if s["service"] == alert.service_name
                    and s.get("span_kind") == "client"
                    and lower <= s["start_time"] < upper
                }
                return list(unique.values())

            timestamp = alert.timestamp.timestamp()
            samples, previous = spans(current, timestamp, end + 0.000001), spans(history, start, timestamp)
            if not samples:
                return {}
            result = {
                "error_rate": sum(s["status"] in {"ERROR", "TIMEOUT"} for s in samples) / len(samples),
                "call_volume": len(samples),
            }
            # A zero timeout rate requires observed protocol outcomes for every sampled call.
            if all("is_timeout" in s for s in samples):
                result["timeout_rate"] = sum(s["is_timeout"] for s in samples) / len(samples)
            # No invented baseline: the current evidence contract defaults missing baseline to 1ms.
            if previous:
                result.update(
                    latency_ms=mean(s["duration_ms"] for s in samples),
                    baseline_latency_ms=mean(s["duration_ms"] for s in previous),
                )
            return result
        return await self.metrics(signal_key, alert, start, end)

    async def metrics(self, key: str, alert: AlertEvent, start: float, end: float) -> dict:
        discovery = await self.prometheus.discover(start, end, alert.service_name)
        names = set(discovery["metric_names"])
        series = discovery["series"]

        def select(aliases, *, scope=None, filters=None):
            for name in aliases:
                if name not in names:
                    continue
                rows = [s for s in series if s.get("__name__") == name]
                match = dict(filters or {})
                if scope:
                    label = next(
                        (
                            label
                            for label in ("service_name", "service.name", "container_name", "container.name")
                            if any(s.get(label) == scope for s in rows)
                        ),
                        None,
                    )
                    if label is None:
                        continue  # Never attribute global/host metrics to an arbitrary service.
                    match[label] = scope
                if not any(all(s.get(k) == v for k, v in match.items()) for s in rows):
                    continue
                return name + "{" + ",".join(label_name(k) + "=" + json.dumps(v) for k, v in match.items()) + "}"
            return None

        queries = {}
        if key == "metric":
            scope = alert.service_name
            calls = select(["traces_span_metrics_calls_total"], scope=scope, filters={"span_kind": "SPAN_KIND_SERVER"})
            errors = select(
                ["traces_span_metrics_calls_total"],
                scope=scope,
                filters={"span_kind": "SPAN_KIND_SERVER", "status_code": "STATUS_CODE_ERROR"},
            )
            if calls:
                queries["qps"] = f"sum(rate({calls}[2m]))"
            if errors and calls:
                queries["error_rate"] = f"100 * sum(rate({errors}[2m])) / sum(rate({calls}[2m]))"
            for aliases, scale in [
                (["traces_span_metrics_duration_milliseconds_bucket"], 1),
                (["http_server_request_duration_seconds_bucket"], 1000),
            ]:
                filters = {"span_kind": "SPAN_KIND_SERVER"} if scale == 1 else None
                bucket = select(aliases, scope=scope, filters=filters)
                if bucket:
                    for pct in (95, 99):
                        queries[f"tp{pct}"] = f"{scale} * histogram_quantile(0.{pct}, sum by (le) (rate({bucket}[2m])))"
                    break
            cpu_time = select(["container_cpu_usage_nanoseconds_total", "container_cpu_usage_total_nanoseconds_total"], scope=scope)
            if cpu_time:
                # Derive current utilization from the monotonic CPU-time counter.
                # Some Docker-exported gauges do not track recent recovery correctly;
                # a scoped counter rate measures the actual recent CPU-time increment.
                queries["cpu_usage"] = f"clamp_max(max(rate({cpu_time}[1m])) / 1000000000, 1)"
            for output, aliases in [
                ("cpu_usage", [("container_cpu_utilization", 100), ("container_cpu_utilization_ratio", 100),
                               ("jvm_cpu_recent_utilization_ratio", 1), ("process_cpu_utilization_ratio", 1)]),
                ("memory_usage", [("container_memory_percent", 100), ("container_memory_percent_ratio", 100)]),
            ]:
                if output in queries:
                    continue
                for alias, scale in aliases:
                    metric = select([alias], scope=scope)
                    if metric:
                        # OTLP translates '%' names to '_ratio' without scaling the samples.
                        # JVM/process CPU metrics already carry a utilization fraction.
                        queries[output] = (
                            f"clamp_max(max({metric}) / {scale}, 1)" if output == "cpu_usage" else f"max({metric}) / {scale}"
                        )
                        break
        elif key == "kafka":
            lag = select(["kafka_consumer_group_lag_sum", "kafka_consumer_group_lag_sum_ratio",
                          "kafka_consumer_group_lag", "kafka_consumer_group_lag_ratio"])
            if lag:
                queries["consumer_lag"] = f"sum({lag})"
        elif key == "db":
            # backends includes background processes: never mislabel it as active connections.
            maximum = select(["postgresql_connection_max_connections", "postgresql_connection_max"])
            if maximum:
                queries["max_connections"] = f"max({maximum})"
        elif key == "redis":
            used = select(["redis_memory_used_bytes", "redis_memory_used"])
            maximum = select(["redis_maxmemory_bytes", "redis_maxmemory"])
            if used:
                queries["used_memory"] = f"sum({used})"
            if used and maximum:
                queries["memory_usage_percent"] = f"100 * sum({used}) / (sum({maximum}) > 0)"
            hits = select(["redis_keyspace_hits_total"])
            misses = select(["redis_keyspace_misses_total"])
            if hits and misses:
                queries["hit_rate_percent"] = (
                    f"100 * sum(rate({hits}[2m])) / (sum(rate({hits}[2m])) + sum(rate({misses}[2m])))"
                )
        else:
            raise ValueError(f"unsupported observation signal: {key}")

        async def query(output, expr):
            matrix = await self.prometheus.query_range(expr, start, end, self.settings.telemetry_step_seconds)
            points = sorted(
                (float(t), float(v)) for row in matrix for t, v in row.get("values", []) if math.isfinite(float(v))
            )
            if not points:
                return output, None
            payload = temporal_payload(points, alert.timestamp.timestamp(), query=expr)
            return output, payload if key in {"metric", "redis", "kafka"} else points[-1][1]

        pairs = await asyncio.gather(*(query(k, q) for k, q in queries.items()))
        observations = {k: v for k, v in pairs if v is not None}
        return observations

    async def read_tool(self, tool_name: str, alert: AlertEvent) -> dict:
        from opspilot.tools.registry import DOMAIN_TOOL_FIELDS, DOMAIN_TOOL_SOURCES

        if tool_name == "db.replication":
            return {}  # Pinned demo has one PostgreSQL instance, no replica.
        if tool_name == "db.slowlog":
            start, end = self.window(alert)
            traces = await self.jaeger.traces(alert.service_name, start, end, limit=self.settings.telemetry_trace_limit)
            slow = [
                s
                for t in traces["traces"]
                for s in t["spans"]
                if s.get("db_system") in {"postgresql", "postgres"} and s["duration_ms"] > 1000
            ]
            spans = {(t["trace_id"], s["span_id"]): s for t in traces["traces"] for s in t["spans"]}
            _, cohorts = database_latency_cohorts(spans, alert.timestamp.timestamp())
            return {"slow_query_count": len(slow), "slow_queries": slow,
                    "database_latency_cohorts": cohorts} if slow else {}
        source = await self.read(DOMAIN_TOOL_SOURCES[tool_name], alert)
        return {field: source[field] for field in DOMAIN_TOOL_FIELDS[tool_name] if field in source}
