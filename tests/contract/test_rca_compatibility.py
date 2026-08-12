from datetime import UTC, datetime

from opspilot.agents import RootCauseAgent
from opspilot.models import AlertEvent, RootCauseType
from opspilot.rca import (
    ExpertRuleEngine,
    MultiDimensionComparator,
    QuantileAnomalyDetector,
    VolatilityDetector,
)


def test_opspilot_rca_namespace_preserves_deterministic_algorithms():
    anomaly = QuantileAnomalyDetector().detect([10, 11, 9, 10, 11, 10, 9, 10, 11, 10], 100)
    assert anomaly.is_anomaly is True
    assert VolatilityDetector(window_size=2).detect([1, 1, 1, 20])
    comparison = MultiDimensionComparator().compare(200, {"last_week": 100, "yesterday": 100})
    assert comparison["is_anomaly"] is True
    assert len(ExpertRuleEngine.RULES) == 8


def test_root_cause_summary_model_failure_uses_deterministic_fallback():
    def failing_summary(*_args):
        raise RuntimeError("summary model unavailable")

    alert = AlertEvent(
        alert_id="fallback-alert",
        service_name="order-service",
        alert_type="custom",
        severity="P3",
        timestamp=datetime(2026, 8, 1, tzinfo=UTC),
    )
    candidates, rationale = RootCauseAgent(summarizer=failing_summary).diagnose(
        alert=alert,
        evidence=[],
    )
    assert candidates[0].root_cause_type == RootCauseType.NO_FAULT
    assert rationale.startswith("Deterministic fallback")
