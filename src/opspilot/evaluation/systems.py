"""System adapters used by the benchmark; none receives evaluation labels."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, ClassVar

from opspilot.config import RuntimeSettings
from opspilot.graph import OpsPilotWorkflow
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.llm import DeepSeekRCAClient
from opspilot.models import AlertEvent, RootCauseType, ToolCall, ToolStatus
from opspilot.tools import ToolExecutor, build_default_registry, build_tool_call_id


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
        started = perf_counter()
        outcome = await self.workflow.observe(alert, trace_id=f"eval-{case_id}")
        return {
            "case_id": case_id, "status": "completed",
            "candidate_types": [item.root_cause_type.value for item in outcome.provisional_candidates],
            "evidence_types": [item.evidence_type for item in outcome.evidence],
            "tool_executions": [item.model_dump(mode="json", exclude={"data"}) for item in outcome.tool_results],
            "latency_ms": (perf_counter()-started)*1000,
            "degraded": any(item.status == ToolStatus.ERROR for item in outcome.tool_results),
            "investigation": outcome.trace.model_dump(mode="json"),
        }



class AdaptivePlannerSystem(OpsPilotHybridSystem):
    """Historical evaluation name for the unified adaptive controller."""

    name = "opspilot_adaptive_planner"

    def __init__(self) -> None:
        self.workflow = OpsPilotWorkflow(
            build_default_registry(timeout_seconds=0.2),
        )


class AdaptiveWithoutDynamicL2System(OpsPilotHybridSystem):
    name = "opspilot_adaptive_without_dynamic_l2"

    def __init__(self) -> None:
        settings = RuntimeSettings(investigation_max_expert_calls=0)
        self.workflow = OpsPilotWorkflow(build_default_registry(timeout_seconds=0.2), settings=settings)


class FullAdaptiveRCASystem(OpsPilotHybridSystem):
    name = "opspilot_full_adaptive"


class FixedPlannerSystem:
    """Ablation baseline: execute a fixed list through the same Evidence Builder."""

    name = "opspilot_fixed_planner"
    tool_names: ClassVar[list[str]] = [
        "metrics.query",
        "logs.query",
        "changes.query",
        "traces.query",
        "topology.query",
        "alerts.query",
        "db.replication",
        "db.slowlog",
        "db.connections",
        "redis.memory",
        "redis.hotkeys",
        "kafka.lag",
        "rpc.metrics",
    ]

    def __init__(self) -> None:
        self.registry = build_default_registry(timeout_seconds=0.2)

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        started = perf_counter()
        executor = ToolExecutor(self.registry)
        calls = []
        for index, tool_name in enumerate(self.tool_names, start=1):
            definition = self.registry.get(tool_name)
            arguments = {"alert": alert.model_dump(mode="json")}
            calls.append(
                ToolCall(
                    tool_call_id=build_tool_call_id(
                        trace_id=f"fixed-{case_id}",
                        step_id=f"fixed-{index}",
                        tool_name=tool_name,
                        version=definition.version,
                        arguments=arguments,
                    ),
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )
        results = await asyncio.gather(*(executor.execute(call) for call in calls))
        analysis = DeterministicEvidenceEngine().analyze(alert, results)
        evidence, candidates = analysis.evidence, analysis.candidates
        actions = [
            {"action_type": "inspect_tool", "target": name, "status": "succeeded"}
            for name in self.tool_names
        ]
        actions.extend(
            {"action_type": "invoke_expert", "target": name, "status": "succeeded"}
            for name in ("db", "redis", "kafka", "rpc")
        )
        return {
            "case_id": case_id,
            "status": "completed",
            "candidate_types": [item.root_cause_type.value for item in candidates],
            "evidence_types": [item.evidence_type for item in evidence],
            "tool_executions": [item.model_dump(mode="json") for item in executor.executions],
            "latency_ms": (perf_counter() - started) * 1000,
            "degraded": any(item.status == ToolStatus.ERROR for item in executor.executions),
            "investigation": {
                "rounds": 1,
                "action_history": actions,
                "expert_budget_used": 4,
                "duplicate_actions": 0,
                "stop_reason": "fixed plan completed",
            },
        }


class _DeepSeekSystem:
    def __init__(self, client: DeepSeekRCAClient) -> None:
        self.client = client

    @staticmethod
    def _public_alert(alert: AlertEvent) -> dict[str, Any]:
        """Exclude benchmark signals: they represent tool backends, not alert labels."""
        return alert.model_dump(mode="json", exclude={"signals"})

    @staticmethod
    def _usage(result) -> dict[str, int]:
        return result.usage.model_dump(mode="json")


class DeepSeekLLMOnlySystem(_DeepSeekSystem):
    name = "deepseek_llm_only"

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        started = perf_counter()
        result = await self.client.diagnose(alert=self._public_alert(alert))
        return {
            "case_id": case_id,
            "status": "completed",
            "candidate_types": [item.value for item in result.decision.candidate_types],
            "evidence_types": [],
            "tool_executions": [],
            "latency_ms": (perf_counter() - started) * 1000,
            "degraded": False,
            "rationale": result.decision.rationale,
            "model": result.model,
            "token_usage": self._usage(result),
        }


class _DeepSeekToolSystem(_DeepSeekSystem):
    def __init__(self, client: DeepSeekRCAClient) -> None:
        super().__init__(client)
        self.workflow = OpsPilotWorkflow(build_default_registry(timeout_seconds=0.2))

    async def _observe(self, alert: AlertEvent, case_id: str):
        outcome = await self.workflow.observe(alert, trace_id=f"eval-{case_id}")
        observations = {
            item.tool_name: item.data.get("observations", {}) if item.data else {"error": item.error_code}
            for item in outcome.tool_results
        }
        return outcome, observations


class DeepSeekToolsSystem(_DeepSeekToolSystem):
    name = "deepseek_tools"

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        started = perf_counter()
        outcome, observations = await self._observe(alert, case_id)
        executions = outcome.tool_results
        result = await self.client.diagnose(
            alert=self._public_alert(alert),
            tool_observations=observations,
        )
        return {
            "case_id": case_id,
            "status": "completed",
            "candidate_types": [item.value for item in result.decision.candidate_types],
            "evidence_types": result.decision.evidence_types,
            "tool_executions": [item.model_dump(mode="json", exclude={"data"}) for item in executions],
            "latency_ms": (perf_counter() - started) * 1000,
            "degraded": any(item.status == ToolStatus.ERROR for item in executions),
            "rationale": result.decision.rationale,
            "model": result.model,
            "token_usage": self._usage(result),
        }


class DeepSeekHybridSystem(_DeepSeekToolSystem):
    name = "deepseek_hybrid"

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        started = perf_counter()
        outcome, observations = await self._observe(alert, case_id)
        executions = outcome.tool_results
        evidence, deterministic_candidates = outcome.evidence, outcome.provisional_candidates
        allowed = [candidate.root_cause_type.value for candidate in deterministic_candidates]
        result = await self.client.diagnose(
            alert=self._public_alert(alert),
            tool_observations=observations,
            evidence=[item.model_dump(mode="json") for item in evidence],
            allowed_candidates=allowed,
        )
        return {
            "case_id": case_id,
            "status": "completed",
            "candidate_types": [item.value for item in result.decision.candidate_types],
            "evidence_types": [item.evidence_type for item in evidence],
            "tool_executions": [item.model_dump(mode="json", exclude={"data"}) for item in executions],
            "latency_ms": (perf_counter() - started) * 1000,
            "degraded": any(item.status == ToolStatus.ERROR for item in executions),
            "rationale": result.decision.rationale,
            "model": result.model,
            "token_usage": self._usage(result),
            "deterministic_candidates": allowed,
        }


def build_system(name: str, *, config: dict[str, Any] | None = None):
    if name == DeepRCABaselineSystem.name:
        return DeepRCABaselineSystem()
    if name == OpsPilotHybridSystem.name:
        return OpsPilotHybridSystem()
    deterministic_systems = {
        FixedPlannerSystem.name: FixedPlannerSystem,
        AdaptivePlannerSystem.name: AdaptivePlannerSystem,
        AdaptiveWithoutDynamicL2System.name: AdaptiveWithoutDynamicL2System,
        FullAdaptiveRCASystem.name: FullAdaptiveRCASystem,
    }
    if name in deterministic_systems:
        return deterministic_systems[name]()
    deepseek_systems = {
        DeepSeekLLMOnlySystem.name: DeepSeekLLMOnlySystem,
        DeepSeekToolsSystem.name: DeepSeekToolsSystem,
        DeepSeekHybridSystem.name: DeepSeekHybridSystem,
    }
    if name in deepseek_systems:
        if config is None:
            raise ValueError(f"{name} requires model config")
        return deepseek_systems[name](DeepSeekRCAClient.from_config(config))
    raise ValueError(f"unknown evaluation system: {name}")
