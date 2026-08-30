"""Stable Stage-1 schemas for online RCA and offline evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlertType(str, Enum):
    TIMEOUT = "timeout"
    ERROR_RATE = "error_rate"
    RESOURCE = "resource"
    CUSTOM = "custom"


class AlertSeverity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AlertEvent(StrictModel):
    """Normalized alert; signals are observed snapshots, never ground truth labels."""

    schema_version: str = "1.0"
    alert_id: str
    service_name: str
    alert_type: AlertType
    severity: AlertSeverity
    timestamp: datetime
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    signals: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_must_have_timezone(self) -> AlertEvent:
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return self


class PlanStep(StrictModel):
    step_id: str
    tool_name: str
    priority: int = Field(ge=1)
    reason: str


class DimensionTask(StrictModel):
    dimension: str
    name: str
    priority: int = Field(ge=1)
    tools: list[str]
    expert_domains: list[str] = Field(default_factory=list)
    reason: str


class AnalysisPlan(StrictModel):
    schema_version: str = "2.0"
    steps: list[PlanStep]
    dimensions: list[DimensionTask] = Field(default_factory=list)


class InvestigationActionType(str, Enum):
    INSPECT_TOOL = "inspect_tool"
    INVOKE_EXPERT = "invoke_expert"
    FINALIZE = "finalize"


class InvestigationAction(StrictModel):
    action_type: InvestigationActionType
    target: str
    reason: str
    round: int = Field(ge=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str = "planned"

    @property
    def identity(self) -> str:
        arguments = sorted((str(key), str(value)) for key, value in self.arguments.items())
        return f"{self.action_type.value}|{self.target}|{arguments}"


class EvidenceGateDecision(StrictModel):
    sufficient: bool
    reason: str
    top1_confidence: float = Field(ge=0, le=1)
    score_margin: float = Field(ge=0, le=1)
    independent_source_count: int = Field(ge=0)
    budget_exhausted: bool = False


class InvestigationTrace(StrictModel):
    rounds: int = Field(ge=1)
    action_history: list[InvestigationAction] = Field(default_factory=list)
    gate_decisions: list[EvidenceGateDecision] = Field(default_factory=list)
    executed_tools: list[str] = Field(default_factory=list)
    invoked_experts: list[str] = Field(default_factory=list)
    stop_reason: str
    tool_budget_used: int = Field(ge=0)
    expert_budget_used: int = Field(ge=0)
    duplicate_actions: int = Field(default=0, ge=0)


class ToolStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class ToolCall(StrictModel):
    schema_version: str = "1.0"
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResult(StrictModel):
    schema_version: str = "1.0"
    tool_call_id: str
    tool_name: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: float = Field(ge=0)
    attempt: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def status_matches_payload(self) -> ToolResult:
        if self.status == ToolStatus.SUCCESS and self.data is None:
            raise ValueError("successful tool result requires data")
        if self.status == ToolStatus.ERROR and not self.error_code:
            raise ValueError("failed tool result requires error_code")
        return self


class ToolExecution(StrictModel):
    tool_call_id: str
    tool_name: str
    status: ToolStatus
    latency_ms: float = Field(ge=0)
    attempt: int = Field(default=1, ge=1)
    error_code: str | None = None


class EvidenceSourceType(str, Enum):
    METRIC = "metric"
    LOG = "log"
    CHANGE = "change"
    TRACE = "trace"
    TOPOLOGY = "topology"
    RULE = "rule"


class EvidenceSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RootCauseType(str, Enum):
    DB_REPLICATION_LAG = "db_replication_lag"
    DB_SLOW_QUERY = "db_slow_query"
    DB_CONNECTION_EXHAUSTED = "db_connection_exhausted"
    REDIS_MEMORY_PRESSURE = "redis_memory_pressure"
    REDIS_LOW_HIT_RATE = "redis_low_hit_rate"
    KAFKA_CONSUMER_LAG = "kafka_consumer_lag"
    RPC_TIMEOUT = "rpc_timeout"
    RPC_ERROR_RATE = "rpc_error_rate"
    BAD_DEPLOYMENT = "bad_deployment"
    RESOURCE_SATURATION = "resource_saturation"
    OOM_RESTART = "oom_restart"
    NO_FAULT = "no_fault"


class Evidence(StrictModel):
    schema_version: str = "1.0"
    evidence_id: str
    evidence_type: str
    source_type: EvidenceSourceType
    source_name: str
    service: str
    observed_at: datetime
    fact: str
    severity: EvidenceSeverity
    confidence: float = Field(ge=0, le=1)
    supports: list[RootCauseType] = Field(default_factory=list)
    contradicts: list[RootCauseType] = Field(default_factory=list)
    raw_ref: str | None = None


class RootCauseCandidate(StrictModel):
    rank: int = Field(ge=1)
    root_cause_type: RootCauseType
    summary: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class DiagnosticFinding(StrictModel):
    finding_type: str
    dimension: str
    service: str
    summary: str
    severity: EvidenceSeverity
    confidence: float = Field(ge=0, le=1)
    data: dict[str, Any] = Field(default_factory=dict)


class SemanticAnalysisResult(StrictModel):
    name: str
    layer: str
    dimension: str
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    error: str | None = None


class AlgorithmSignal(StrictModel):
    algorithm: str
    metric: str
    signal_type: str
    is_anomaly: bool
    confidence: float = Field(ge=0, le=1)
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosisReport(StrictModel):
    schema_version: str = "1.0"
    trace_id: str
    alert_id: str
    service_name: str
    status: str = "completed"
    candidates: list[RootCauseCandidate]
    primary_root_cause: RootCauseCandidate
    evidence: list[Evidence]
    tool_executions: list[ToolExecution]
    dimension_results: list[SemanticAnalysisResult] = Field(default_factory=list)
    expert_results: list[SemanticAnalysisResult] = Field(default_factory=list)
    algorithm_signals: list[AlgorithmSignal] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    investigation: InvestigationTrace | None = None
    llm_used: bool = False
    degraded: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    decision_rationale: str
    recommended_actions: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    latency_ms: float = Field(ge=0)


class EvaluationCase(StrictModel):
    schema_version: str = "1.0"
    case_id: str
    dataset_version: str
    split: str
    category: str
    scenario_seed: int
    alert: AlertEvent
    expected_root_cause_type: RootCauseType
    expected_evidence_types: list[str]
    expected_tools: list[str]
    is_fault: bool
    tags: list[str] = Field(default_factory=list)
