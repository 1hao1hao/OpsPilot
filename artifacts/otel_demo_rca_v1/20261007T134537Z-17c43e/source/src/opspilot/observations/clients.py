"""Backend HTTP clients and wire-format adapters."""

import json
import math
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import httpx

from opspilot.tools.errors import ToolExecutionError
from opspilot.tracing.normalizer import normalize_trace_payload


def label_name(name: str) -> str:
    """Quote Prometheus 3 UTF-8 label names when legacy syntax cannot represent them."""
    return name if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name) else json.dumps(name)


class BackendError(ToolExecutionError):
    def __init__(self, backend: str, detail: str):
        self.code = f"{backend}_unavailable"
        super().__init__(f"{self.code}: {detail}")


class BackendClient:
    backend = "telemetry"

    def __init__(self, url: str, *, timeout: float = 5, transport=None):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.request(method, self.url + path, **kwargs)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise TypeError("expected JSON object")
                return data
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            # Avoid including backend URLs/credentials or full response bodies.
            raise BackendError(self.backend, type(exc).__name__) from exc


class PrometheusClient(BackendClient):
    backend = "prometheus"

    async def api(self, path: str, params=None):
        payload = await self.request("GET", "/api/v1/" + path, params=params)
        if payload.get("status") != "success" or "data" not in payload:
            raise BackendError(self.backend, "unsuccessful API response")
        return payload["data"]

    async def discover(self, start: float, end: float, service: str | None = None) -> dict:
        params = {"start": start, "end": end}
        names = await self.api("label/__name__/values", params)
        labels = await self.api("labels", params)
        if not all(isinstance(values, list) and all(isinstance(v, str) for v in values) for values in (names, labels)):
            raise BackendError(self.backend, "invalid discovery labels")
        # Service metrics plus receiver/container metrics (which may not carry service.name).
        selectors = ['{__name__=~"(traces_span_metrics|http_server|rpc_server|container|redis|kafka|postgresql)_.*"}']
        if service:
            selectors += [
                "{" + label_name(label) + "=" + json.dumps(service) + "}"
                for label in ("service_name", "service.name")
                if label in labels
            ]
        series = await self.api("series", list(params.items()) + [("match[]", s) for s in selectors])
        if not isinstance(series, list) or not all(isinstance(row, dict) for row in series):
            raise BackendError(self.backend, "invalid discovery response")
        return {
            "metric_names": names,
            "labels": labels,
            "series": series,
            "start": start,
            "end": end,
            "service": service,
            "source": "prometheus_api",
        }

    async def query_range(self, query: str, start: float, end: float, step: int):
        data = await self.api("query_range", {"query": query, "start": start, "end": end, "step": step})
        if not isinstance(data, dict) or data.get("resultType") != "matrix" or not isinstance(data.get("result"), list):
            raise BackendError(self.backend, "expected range matrix")
        try:
            for row in data["result"]:
                for timestamp, value in row["values"]:
                    if not math.isfinite(float(timestamp)):
                        raise ValueError("non-finite timestamp")
                    float(value)  # NaN/Inf samples are valid Prometheus values, filtered by the adapter.
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendError(self.backend, "invalid range samples") from exc
        return data["result"]


class JaegerClient(BackendClient):
    backend = "jaeger"

    async def traces(self, service: str, start: float, end: float, *, limit=50, error=False, min_duration_ms=None):
        params: dict[str, Any] = {"service": service, "start": int(start * 1e6), "end": int(end * 1e6), "limit": limit}
        if error:
            params["tags"] = json.dumps({"error": "true"})
        if min_duration_ms is not None:
            params["minDuration"] = f"{min_duration_ms}ms"
        try:
            payload = await self.request("GET", "/api/traces", params=params)
        except BackendError as exc:
            cause = exc.__cause__
            if not isinstance(cause, httpx.HTTPStatusError) or cause.response.status_code != 404:
                raise
            # Jaeger 2.21 removed legacy search, but retains the UI trace-by-ID format.
            query = {
                "query.serviceName": service,
                "query.startTimeMin": datetime.fromtimestamp(start, UTC).isoformat(),
                "query.startTimeMax": datetime.fromtimestamp(end, UTC).isoformat(),
                "query.searchDepth": limit,
            }
            if error:
                query["query.attributes"] = json.dumps({"error": "true"})
            if min_duration_ms is not None:
                query["query.durationMin"] = f"{min_duration_ms}ms"
            try:
                response = await self.request("GET", "/api/v3/traces", params=query)
            except BackendError as search_error:
                response_error = search_error.__cause__
                if isinstance(response_error, httpx.HTTPStatusError) and response_error.response.status_code == 404:
                    try:
                        no_results = response_error.response.json().get("error", {}).get("message") == "No traces found"
                    except (ValueError, AttributeError):
                        no_results = False
                    if no_results:
                        return {"traces": []}
                raise
            payload = response.get("result")
            if not isinstance(payload, dict) or not isinstance(payload.get("resourceSpans"), list):
                raise BackendError(self.backend, "invalid OTLP trace response")
            try:
                return self.adapt(payload, limit)
            except (KeyError, TypeError, ValueError, AttributeError) as invalid:
                raise BackendError(self.backend, "invalid trace records") from invalid
        if payload.get("errors") or not isinstance(payload.get("data"), list):
            raise BackendError(self.backend, "invalid trace response")
        try:
            return self.adapt(payload, limit)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise BackendError(self.backend, "invalid trace records") from exc

    @staticmethod
    def adapt(payload: dict, limit: int) -> dict:
        # RCA receives its existing native schema, never Jaeger wire JSON.
        native = {
            "traces": [
                {"trace_id": t.trace_id, "spans": [asdict(s) for s in t.spans]}
                for t in normalize_trace_payload(payload)[:limit]
            ]
        }
        otlp = "resourceSpans" in payload
        if otlp:
            raw_spans = {
                (s["traceId"], s["spanId"]): s
                for resource in payload["resourceSpans"]
                for scope in resource.get("scopeSpans", [])
                for s in scope.get("spans", [])
            }
        else:
            raw_spans = {(t["traceID"], s["spanID"]): s for t in payload["data"] for s in t.get("spans", [])}
        for trace in native["traces"]:
            for span in trace["spans"]:
                raw = raw_spans[(trace["trace_id"], span["span_id"])]
                if otlp:
                    attrs = {x["key"]: next(iter(x.get("value", {}).values()), None)
                             for x in raw.get("attributes", [])}
                    span["start_time"] = float(raw["startTimeUnixNano"]) / 1e9
                    span["duration_ms"] = (float(raw["endTimeUnixNano"]) - float(raw["startTimeUnixNano"])) / 1e6
                    attrs["span.kind"] = {1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}.get(
                        raw.get("kind"), str(raw.get("kind", "")).removeprefix("SPAN_KIND_").lower()
                    )
                else:
                    attrs = {x["key"]: x.get("value") for x in raw.get("tags", [])}
                    span["start_time"] = float(raw["startTime"]) / 1e6
                    span["duration_ms"] = float(raw["duration"]) / 1000
                if (
                    not math.isfinite(span["start_time"])
                    or not math.isfinite(span["duration_ms"])
                    or span["duration_ms"] < 0
                ):
                    raise ValueError("non-finite span timing")
                span["span_kind"] = str(attrs.get("span.kind", "")).lower()
                span["db_system"] = attrs.get("db.system.name", attrs.get("db.system"))
                grpc = attrs.get("rpc.response.status_code", attrs.get("rpc.grpc.status_code"))
                http = attrs.get("http.response.status_code", attrs.get("http.status_code"))
                # Omit timeout rather than inventing zero when no protocol status was observed.
                if grpc is not None or http is not None:
                    span["is_timeout"] = str(grpc).upper() in {"4", "DEADLINE_EXCEEDED"} or str(http) == "504"
                    if span["is_timeout"]:
                        span["status"] = "TIMEOUT"
        return native


def field(data: dict, key: str, default=None):
    if key in data:
        return data[key]
    result = data
    for part in key.split("."):
        if not isinstance(result, dict) or part not in result:
            return default
        result = result[part]
    return result


class OpenSearchClient(BackendClient):
    backend = "opensearch"

    async def logs(self, index: str, service: str, start: str, end: str, limit: int):
        query = {
            "size": limit,
            "_source": ["observedTimestamp", "resource.service.name", "severity.text", "body"],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"resource.service.name.keyword": service}},
                        {"range": {"observedTimestamp": {"gte": start, "lte": end}}},
                    ],
                    "should": [
                        {"terms": {"severity.text.keyword": ["ERROR", "WARN", "FATAL"]}},
                        {"match": {"body": "exception timeout error"}},
                    ],
                }
            },
            "sort": [{"_score": "desc"}, {"observedTimestamp": "desc"}],
        }
        payload = await self.request("POST", f"/{index}/_search", json=query)
        shards, hit_data = payload.get("_shards", {}), payload.get("hits", {})
        if not isinstance(shards, dict) or not isinstance(hit_data, dict):
            raise BackendError(self.backend, "invalid log response")
        if payload.get("timed_out") or shards.get("failed", 0):
            raise BackendError(self.backend, "incomplete log search")
        hits = hit_data.get("hits")
        if not isinstance(hits, list):
            raise BackendError(self.backend, "invalid log response")
        try:
            return self.adapt(hits, limit, service)
        except (KeyError, TypeError, AttributeError) as exc:
            raise BackendError(self.backend, "invalid log records") from exc

    @staticmethod
    def adapt(hits: list, limit: int, service: str) -> dict:
        for hit in hits[:limit]:
            if not isinstance(hit["_source"], dict) or field(hit["_source"], "observedTimestamp") is None:
                raise TypeError("log record requires source and timestamp")
        return {
            "logs": [
                {
                    "timestamp": field(h["_source"], "observedTimestamp"),
                    "service": field(h["_source"], "resource.service.name", service),
                    "severity": field(h["_source"], "severity.text", "UNSPECIFIED"),
                    "message": str(field(h["_source"], "body", ""))[:2000],
                }
                for h in hits[:limit]
            ]
        }
