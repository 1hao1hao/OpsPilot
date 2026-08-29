"""Normalize vendor trace payloads before rebuilding span trees."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service: str
    operation: str
    duration_ms: float
    status: str


@dataclass(frozen=True)
class NormalizedTrace:
    trace_id: str
    spans: tuple[NormalizedSpan, ...]


@dataclass(frozen=True)
class SpanAnomaly:
    trace_id: str
    span_id: str
    service: str
    operation: str
    path: tuple[str, ...]
    duration_ms: float
    status: str
    is_error: bool
    is_slow: bool


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _status(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("code", value.get("statusCode", value.get("message", "UNSET")))
    if isinstance(value, int):
        return {0: "UNSET", 1: "OK", 2: "ERROR"}.get(value, str(value))
    text = str(value or "UNSET").upper()
    aliases = {"STATUS_CODE_OK": "OK", "STATUS_CODE_ERROR": "ERROR", "SUCCESS": "OK"}
    return aliases.get(text, text)


def _attribute_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return value[key]
    return value.get("value", value)


def _attributes(items: Any) -> dict[str, Any]:
    if isinstance(items, dict):
        return items
    result: dict[str, Any] = {}
    for item in items or []:
        if isinstance(item, dict) and item.get("key"):
            result[str(item["key"])] = _attribute_value(item.get("value"))
    return result


def _normalize_native(payload: dict[str, Any]) -> list[NormalizedTrace]:
    raw_traces = payload.get("traces", [])
    traces: list[NormalizedTrace] = []
    for trace_index, raw_trace in enumerate(raw_traces):
        if not isinstance(raw_trace, dict):
            continue
        trace_id = str(raw_trace.get("trace_id", raw_trace.get("traceId", f"trace-{trace_index}")))
        raw_spans = raw_trace.get("spans", [])
        spans: list[NormalizedSpan] = []
        root_id: str | None = None
        for span_index, raw_span in enumerate(raw_spans):
            if not isinstance(raw_span, dict):
                continue
            span_id = str(raw_span.get("span_id", raw_span.get("spanId", f"legacy-{span_index}")))
            parent = raw_span.get("parent_span_id", raw_span.get("parentSpanId"))
            # Old mock/test payloads omitted IDs entirely. Preserve compatibility by
            # attaching later anonymous spans to the first anonymous root.
            if span_index == 0:
                root_id = span_id
            elif parent in (None, "") and "span_id" not in raw_span and "spanId" not in raw_span:
                parent = root_id
            spans.append(
                NormalizedSpan(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=str(parent) if parent not in (None, "") else None,
                    service=str(raw_span.get("service", raw_span.get("serviceName", "unknown"))),
                    operation=str(raw_span.get("operation", raw_span.get("operationName", raw_span.get("name", "")))),
                    duration_ms=_float(raw_span.get("duration_ms", raw_span.get("durationMs", raw_span.get("duration", 0)))),
                    status=_status(raw_span.get("status")),
                )
            )
        traces.append(NormalizedTrace(trace_id=trace_id, spans=tuple(spans)))
    return traces


def _normalize_jaeger(payload: dict[str, Any]) -> list[NormalizedTrace]:
    traces: list[NormalizedTrace] = []
    for trace_index, raw_trace in enumerate(payload.get("data", [])):
        if not isinstance(raw_trace, dict):
            continue
        trace_id = str(raw_trace.get("traceID", f"trace-{trace_index}"))
        processes = raw_trace.get("processes", {})
        spans: list[NormalizedSpan] = []
        for raw_span in raw_trace.get("spans", []):
            refs = raw_span.get("references", [])
            parent = next((ref.get("spanID") for ref in refs if ref.get("refType") == "CHILD_OF"), None)
            process = processes.get(raw_span.get("processID"), {})
            tags = _attributes(raw_span.get("tags", []))
            status = "ERROR" if tags.get("error") is True else tags.get("otel.status_code", "OK")
            spans.append(
                NormalizedSpan(
                    trace_id=trace_id,
                    span_id=str(raw_span.get("spanID", "")),
                    parent_span_id=str(parent) if parent else None,
                    service=str(process.get("serviceName", "unknown")),
                    operation=str(raw_span.get("operationName", "")),
                    duration_ms=_float(raw_span.get("duration")) / 1000.0,
                    status=_status(status),
                )
            )
        traces.append(NormalizedTrace(trace_id=trace_id, spans=tuple(spans)))
    return traces


def _normalize_otel(payload: dict[str, Any]) -> list[NormalizedTrace]:
    grouped: dict[str, list[NormalizedSpan]] = {}
    for resource_spans in payload.get("resourceSpans", []):
        resource = resource_spans.get("resource", {})
        resource_attrs = _attributes(resource.get("attributes", []))
        service = str(resource_attrs.get("service.name", "unknown"))
        scopes = resource_spans.get("scopeSpans", resource_spans.get("instrumentationLibrarySpans", []))
        for scope in scopes:
            for raw_span in scope.get("spans", []):
                trace_id = str(raw_span.get("traceId", ""))
                parent = raw_span.get("parentSpanId")
                start = _float(raw_span.get("startTimeUnixNano"))
                end = _float(raw_span.get("endTimeUnixNano"))
                grouped.setdefault(trace_id, []).append(
                    NormalizedSpan(
                        trace_id=trace_id,
                        span_id=str(raw_span.get("spanId", "")),
                        parent_span_id=str(parent) if parent not in (None, "") else None,
                        service=service,
                        operation=str(raw_span.get("name", "")),
                        duration_ms=max(end - start, 0.0) / 1_000_000.0,
                        status=_status(raw_span.get("status")),
                    )
                )
    return [NormalizedTrace(trace_id=trace_id, spans=tuple(spans)) for trace_id, spans in grouped.items()]


def normalize_trace_payload(payload: dict[str, Any]) -> list[NormalizedTrace]:
    """Adapt Mock/internal, Jaeger JSON, or OTLP JSON to one internal model."""
    if payload.get("resourceSpans") is not None:
        return _normalize_otel(payload)
    if payload.get("data") is not None:
        return _normalize_jaeger(payload)
    return _normalize_native(payload)


def _service_path(span: NormalizedSpan, spans_by_id: dict[str, NormalizedSpan]) -> tuple[str, ...]:
    chain: list[str] = []
    current: NormalizedSpan | None = span
    visited: set[str] = set()
    while current and current.span_id not in visited:
        visited.add(current.span_id)
        if not chain or chain[-1] != current.service:
            chain.append(current.service)
        current = spans_by_id.get(current.parent_span_id or "")
    return tuple(reversed(chain))


def detect_span_anomalies(payload: dict[str, Any], *, slow_threshold_ms: float = 1000.0) -> list[SpanAnomaly]:
    """Inspect every child span and retain its root-to-failure service path."""
    error_statuses = {"ERROR", "FAILED", "TIMEOUT", "CANCELLED", "ABORTED"}
    anomalies: list[SpanAnomaly] = []
    for trace in normalize_trace_payload(payload):
        by_id = {span.span_id: span for span in trace.spans if span.span_id}
        for span in trace.spans:
            if not span.parent_span_id:
                continue
            is_error = span.status in error_statuses
            is_slow = span.status == "SLOW" or span.duration_ms > slow_threshold_ms
            if not is_error and not is_slow:
                continue
            anomalies.append(
                SpanAnomaly(
                    trace_id=trace.trace_id,
                    span_id=span.span_id,
                    service=span.service,
                    operation=span.operation,
                    path=_service_path(span, by_id),
                    duration_ms=span.duration_ms,
                    status=span.status,
                    is_error=is_error,
                    is_slow=is_slow,
                )
            )
    return sorted(anomalies, key=lambda item: (item.trace_id, item.path, item.span_id))
