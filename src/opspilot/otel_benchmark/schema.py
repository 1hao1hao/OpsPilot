"""Strict controller-only dataset and symptom-only alert construction."""

import uuid
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from opspilot.models import AlertEvent, RootCauseType
from opspilot.models.schemas import StrictModel


class FaultControl(StrictModel):
    flag: str
    variant: str


class GroundTruth(StrictModel):
    root_cause_type: RootCauseType
    root_service: str
    mechanism: str
    taxonomy_note: str = ""


class AlertTemplate(StrictModel):
    service_name: str
    alert_type: str
    description: str
    severity: str = "P2"


class Scenario(StrictModel):
    scenario_id: str
    fault_control: FaultControl | None
    ground_truth: GroundTruth
    alert_template: AlertTemplate
    tags: list[str] = Field(default_factory=list)
    timing_overrides: dict[str, int] = Field(default_factory=dict)
    recovery_restart_services: list[str] = Field(default_factory=list)
    preparation_restart_services: list[str] = Field(default_factory=list)


class Profile(StrictModel):
    warmup_seconds: int = Field(ge=15)
    fault_stabilization_seconds: int = Field(ge=0)
    observation_window_seconds: int = Field(ge=1)
    cooldown_seconds: int = Field(ge=15)
    repeat: int = Field(ge=1)
    normal_repeat: int = Field(ge=3)
    recovery_timeout_seconds: int = Field(default=300, ge=15)
    baseline_timeout_seconds: int = Field(default=180, ge=15)
    max_memory_fraction: float = Field(default=0.85, gt=0, lt=1)


class Dataset(StrictModel):
    schema_version: str
    dataset_version: str
    upstream_version: dict[str, str]
    load: dict[str, int]
    profiles: dict[str, Profile]
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def fixed_cases(self):
        if len(self.scenarios) != 7 or len({s.scenario_id for s in self.scenarios}) != 7:
            raise ValueError("v1 requires seven unique cases")
        if sum(s.fault_control is None for s in self.scenarios) != 1:
            raise ValueError("v1 requires six faults and one normal")
        flags = {s.fault_control.flag for s in self.scenarios if s.fault_control}
        if flags != {"adHighCpu", "paymentFailure", "paymentUnreachable", "kafkaQueueProblems",
                     "productCatalogLockContention", "emailMemoryLeak"}:
            raise ValueError("v1 requires the six specified upstream flags")
        if any(s.ground_truth.root_cause_type != RootCauseType.NO_FAULT for s in self.scenarios if not s.fault_control):
            raise ValueError("Normal control requires NO_FAULT ground truth")
        if self.profiles["release"].repeat < 3:
            raise ValueError("release must support at least three repetitions")
        return self


def load_dataset(path: Path) -> Dataset:
    return Dataset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def make_alert(template: AlertTemplate, timestamp: datetime) -> AlertEvent:
    # Explicit allowlist; no Scenario, controls or truth fields enter this constructor.
    return AlertEvent(alert_id=f"alert-{uuid.uuid4().hex}", timestamp=timestamp,
                      **template.model_dump())
