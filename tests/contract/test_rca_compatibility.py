from datetime import UTC, datetime

from opspilot.agents import RootCauseAgent
from opspilot.models import AlertEvent, RootCauseType


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
