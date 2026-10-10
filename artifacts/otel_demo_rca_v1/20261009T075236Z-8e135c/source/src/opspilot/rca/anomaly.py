"""Historical baseline, IQR and rolling volatility checks producing Evidence."""

from typing import Any

import numpy as np

from opspilot.evidence.collector import _evidence
from opspilot.evidence.temporal import change
from opspilot.models import (
    AlertEvent,
    Evidence,
    EvidenceSeverity,
    EvidenceSourceType,
    RootCauseType,
    ToolResult,
    ToolStatus,
)
from opspilot.rca.comparator import MultiDimensionComparator
from opspilot.rca.quantile import QuantileAnomalyDetector


def metric_view(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics = {}
    for name in ("qps", "error_rate", "tp99", "tp95", "cpu_usage", "memory_usage", "disk_usage"):
        value = raw.get(name)
        if isinstance(value, dict):
            payload = dict(value)
        elif isinstance(value, (int, float)):
            payload = {"current": float(value)}
        elif raw.get("metric") == name:
            payload = dict(raw)
        else:
            continue
        series = [
            float(point["value"] if isinstance(point, dict) else point) for point in payload.get("data_points", [])
        ]
        aggregation = payload.get("aggregation", {})
        payload.setdefault("current", payload.get("value", aggregation.get("current", series[-1] if series else None)))
        payload.setdefault("baseline_series", series[:-1])
        payload.setdefault("time_series", series)
        payload.setdefault("baseline", aggregation.get("baseline"))
        metrics[name] = payload
    return metrics


def _supports(metric: str) -> list[RootCauseType]:
    if metric in {"cpu_usage", "memory_usage", "disk_usage", "qps"}:
        return [RootCauseType.RESOURCE_SATURATION]
    if metric == "error_rate":
        return [RootCauseType.RPC_ERROR_RATE]
    return [RootCauseType.RPC_TIMEOUT]


class AnomalyDetector:
    """Numerical helper results remain local; callers receive only Evidence."""

    def detect(self, alert: AlertEvent, results: list[ToolResult]) -> list[Evidence]:
        evidence = []
        for result in results:
            if result.tool_name != "metrics.query" or result.status != ToolStatus.SUCCESS or not result.data:
                continue
            for metric, payload in metric_view(result.data.get("observations", {})).items():
                current = payload.get("current")
                reference = payload.get("reference_series")
                if reference:
                    payload["baseline_series"] = reference
                    payload["baseline"] = float(np.median(reference))
                if payload.get("temporal_context"):
                    is_resource = metric in {"cpu_usage", "memory_usage", "disk_usage"}
                    shift = change(payload, minimum_delta=0.05 if is_resource else 100 if metric in {"tp95", "tp99"} else 1,
                                   minimum_ratio=1.1 if is_resource else 3 if metric in {"tp95", "tp99"} else 1.5,
                                   reset_aware=is_resource)
                    if not shift:
                        continue

                def emit(method: str, fact: str, confidence: float, *, result=result, metric=metric) -> None:
                    evidence.append(
                        _evidence(
                            alert=alert,
                            source_name=result.tool_name,
                            source_group=f"tool:{result.tool_name}:{result.tool_call_id}",
                            evidence_type=f"anomaly.{method}",
                            source_type=EvidenceSourceType.METRIC,
                            fact=f"{metric}: {fact}",
                            severity=EvidenceSeverity.WARNING,
                            confidence=confidence,
                            supports=_supports(metric),
                            raw_ref=result.tool_call_id,
                        )
                    )

                if current is not None:
                    current = float(current)
                    baselines = {
                        "last_week": payload.get("last_week"),
                        "yesterday": payload.get("yesterday", payload.get("baseline")),
                    }
                    # Falling latency/error/resource use is recovery, not a fault.
                    # Throughput changes alone do not establish resource saturation.
                    if metric == "qps":
                        continue
                    known = [float(v) for v in baselines.values() if v is not None]
                    minimum_ratio = 3.0 if metric in {"tp95", "tp99"} else 1.2
                    material = not known or current > max(known) * minimum_ratio
                    if metric in {"cpu_usage", "memory_usage", "disk_usage"} and known:
                        material = material and current - max(known) > 0.01
                    comparison = MultiDimensionComparator().compare(current, baselines)
                    if comparison["is_anomaly"] and material:
                        emit("historical_baseline", f"current={current}, historical baselines={baselines}", 0.85)
                    baseline = payload.get("baseline_series", [])
                    if baseline:
                        detected = QuantileAnomalyDetector().detect([float(v) for v in baseline], current)
                        if detected.is_anomaly and detected.anomaly_type != "drop" and material:
                            emit(
                                "iqr",
                                f"IQR {detected.anomaly_type}, current={current}, "
                                f"median={detected.baseline_value}, bounds="
                                f"[{detected.details['lower_bound']}, {detected.details['upper_bound']}]",
                                detected.confidence,
                            )
                series = payload.get("time_series", [])
                # Non-overlapping historical/recent windows include the newest sample.
                # The previous rolling helper omitted that sample and fabricated a
                # zero baseline when only two windows were present.
                window = 10
                if len(series) >= window * 2:
                    rolling = [float(np.std(series[i - window : i])) for i in range(window, len(series) - window + 1)]
                    baseline_std = float(np.mean(rolling))
                    recent_std = float(np.std(series[-window:]))
                    if recent_std > 3 * baseline_std:
                        emit("volatility", f"rolling baseline std={baseline_std:.4f}, recent std={recent_std:.4f}", 0.8)
        return evidence
