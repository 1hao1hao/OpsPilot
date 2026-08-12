"""System adapters used by the benchmark; neither receives evaluation labels."""

from __future__ import annotations

from time import perf_counter

from opspilot.graph import OpsPilotWorkflow
from opspilot.models import AlertEvent, RootCauseType
from opspilot.tools import build_default_registry


class DeepRCABaselineSystem:
    name = "deeprca_baseline"

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        started = perf_counter()
        text = alert.description.lower()
        rules = [
            (("replication", "主从"), RootCauseType.DB_REPLICATION_LAG),
            (("slow query", "慢查询"), RootCauseType.DB_SLOW_QUERY),
            (("connection", "连接池"), RootCauseType.DB_CONNECTION_EXHAUSTED),
            (("cache", "redis"), RootCauseType.REDIS_MEMORY_PRESSURE),
            (("kafka", "consumer lag", "消息积压"), RootCauseType.KAFKA_CONSUMER_LAG),
            (("rpc", "dependency calls are timing out"), RootCauseType.RPC_TIMEOUT),
            (("downstream responses are failing",), RootCauseType.RPC_ERROR_RATE),
            (("release", "deployment"), RootCauseType.BAD_DEPLOYMENT),
            (("capacity", "resource"), RootCauseType.RESOURCE_SATURATION),
            (("oom", "outofmemory"), RootCauseType.OOM_RESTART),
        ]
        cause = RootCauseType.NO_FAULT
        for keywords, candidate in rules:
            if any(keyword in text for keyword in keywords):
                cause = candidate
                break
        return {
            "case_id": case_id,
            "status": "completed",
            "candidate_types": [cause.value],
            "evidence_types": [],
            "tool_executions": [],
            "latency_ms": (perf_counter() - started) * 1000,
            "degraded": False,
        }


class OpsPilotHybridSystem:
    name = "opspilot_hybrid"

    def __init__(self) -> None:
        self.workflow = OpsPilotWorkflow(build_default_registry(timeout_seconds=0.2))

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        report = await self.workflow.analyze(alert, trace_id=f"eval-{case_id}")
        return {
            "case_id": case_id,
            "status": report.status,
            "candidate_types": [item.root_cause_type.value for item in report.candidates],
            "evidence_types": [item.evidence_type for item in report.evidence],
            "tool_executions": [item.model_dump(mode="json") for item in report.tool_executions],
            "latency_ms": report.latency_ms,
            "degraded": report.degraded,
        }


def build_system(name: str):
    if name == DeepRCABaselineSystem.name:
        return DeepRCABaselineSystem()
    if name == OpsPilotHybridSystem.name:
        return OpsPilotHybridSystem()
    raise ValueError(f"unknown evaluation system: {name}")

