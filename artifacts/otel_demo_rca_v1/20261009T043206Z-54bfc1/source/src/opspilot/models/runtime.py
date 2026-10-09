"""Public runtime contracts; ORM models deliberately live elsewhere."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from opspilot.models.schemas import AlertEvent, DiagnosisReport, StrictModel


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ToolExecutionStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CreateRunRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    alert: AlertEvent


class RunAccepted(StrictModel):
    run_id: str
    request_id: str
    status: RunStatus


class RunView(StrictModel):
    run_id: str
    request_id: str
    alert_id: str
    status: RunStatus
    current_step: str | None
    attempt: int
    graph_version: str
    config_version: str
    recovered_count: int
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunResult(StrictModel):
    run_id: str
    status: RunStatus
    report: DiagnosisReport | None = None


class RuntimeEventView(StrictModel):
    sequence: int
    event_type: str
    status: str | None
    step_name: str | None
    detail: dict = Field(default_factory=dict)
    created_at: datetime
