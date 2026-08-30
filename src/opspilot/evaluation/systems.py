"""System adapters used by the benchmark; none receives evaluation labels."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, ClassVar

from opspilot.agents import CoordinatorAgent, RootCauseAgent, analyze_dimensions, analyze_experts
from opspilot.config import RuntimeSettings
from opspilot.evaluation.deepseek import DeepSeekRCAClient
from opspilot.evidence import collect_evidence, collect_expert_evidence, collect_semantic_evidence
from opspilot.graph import OpsPilotWorkflow
from opspilot.investigation import AdaptiveInvestigator
from opspilot.models import AlertEvent, AnalysisPlan, DimensionTask, PlanStep, RootCauseType, ToolCall, ToolStatus
from opspilot.rca.pipeline import run_deterministic_pipeline
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
        report = await self.workflow.analyze(alert, trace_id=f"eval-{case_id}")
        return {
            "case_id": case_id,
            "status": report.status,
            "candidate_types": [item.root_cause_type.value for item in report.candidates],
            "evidence_types": [item.evidence_type for item in report.evidence],
            "tool_executions": [item.model_dump(mode="json") for item in report.tool_executions],
            "latency_ms": report.latency_ms,
            "degraded": report.degraded,
            "investigation": report.investigation.model_dump(mode="json") if report.investigation else None,
        }


class AdaptivePlannerSystem(OpsPilotHybridSystem):
    """Adaptive controller with raw Tool evidence, before L2 Finding promotion."""

    name = "opspilot_adaptive_planner"

    def __init__(self) -> None:
        self.workflow = OpsPilotWorkflow(
            build_default_registry(timeout_seconds=0.2),
            promote_expert_evidence=False,
        )


class AdaptiveWithoutDynamicL2System(OpsPilotHybridSystem):
    name = "opspilot_adaptive_without_dynamic_l2"

    def __init__(self) -> None:
        settings = RuntimeSettings(investigation_max_expert_calls=0)
        self.workflow = OpsPilotWorkflow(build_default_registry(timeout_seconds=0.2), settings=settings)


class FullAdaptiveRCASystem(OpsPilotHybridSystem):
    name = "opspilot_full_adaptive"


class FixedPlannerSystem:
    """Ablation baseline: execute all six dimensions and all legacy Experts."""

    name = "opspilot_fixed_planner"
    tool_names: ClassVar[list[str]] = [
        "metrics.query",
        "logs.query",
        "changes.query",
        "traces.query",
        "topology.query",
        "alerts.query",
        "db.inspect",
        "redis.inspect",
        "kafka.inspect",
        "rpc.inspect",
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
        dimensions = ["change", "upstream", "downstream", "cluster", "errorlog", "problem"]
        plan = AnalysisPlan(
            steps=[
                PlanStep(step_id=f"fixed-{index}", tool_name=name, priority=index, reason="fixed ablation")
                for index, name in enumerate(self.tool_names, start=1)
            ],
            dimensions=[
                DimensionTask(
                    dimension=name,
                    name=name,
                    priority=index,
                    tools=[],
                    reason="fixed six-dimension ablation",
                )
                for index, name in enumerate(dimensions, start=1)
            ],
        )
        dimension_results = analyze_dimensions(alert, plan, results)
        expert_results = analyze_experts(alert, results)
        evidence = collect_evidence(alert, results)
        evidence.extend(collect_semantic_evidence(alert, dimension_results))
        evidence.extend(collect_expert_evidence(alert, expert_results))
        _signals, deterministic_evidence, _rules = run_deterministic_pipeline(
            alert, results, dimension_results, expert_results, evidence
        )
        evidence.extend(deterministic_evidence)
        evidence = sorted(
            {item.evidence_id: item for item in evidence}.values(),
            key=lambda item: (-item.confidence, item.evidence_id),
        )
        candidates, _ = RootCauseAgent().diagnose(alert, evidence)
        actions = [
            {"action_type": "inspect_tool", "target": name, "status": "succeeded"}
            for name in self.tool_names
        ]
        actions.extend(
            {"action_type": "invoke_expert", "target": name, "status": "succeeded"}
            for name in ("db", "redis", "kafka", "rpc")
        )
        actions.append({"action_type": "finalize", "target": "finalize", "status": "succeeded"})
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
        self.registry = build_default_registry(timeout_seconds=0.2)
        self.coordinator = CoordinatorAgent(self.registry)
        self.investigator = AdaptiveInvestigator(self.registry)

    async def _observe(self, alert: AlertEvent, case_id: str):
        executor = ToolExecutor(self.registry)

        async def execute_tool(tool_name: str, round_number: int, reason: str):
            definition = self.registry.get(tool_name)
            arguments = {"alert": alert.model_dump(mode="json")}
            call = ToolCall(
                tool_call_id=build_tool_call_id(
                    trace_id=f"eval-{case_id}",
                    step_id=f"round-{round_number}:{tool_name}",
                    tool_name=tool_name,
                    version=definition.version,
                    arguments=arguments,
                ),
                tool_name=tool_name,
                arguments=arguments,
            )
            return await executor.execute(call)

        outcome = await self.investigator.run(alert, execute_tool, parallel_seed=True)
        results = outcome.tool_results
        observations = {
            item.tool_name: item.data.get("observations", {}) if item.data else {"error": item.error_code}
            for item in results
        }
        return self.coordinator.plan(alert), results, executor.executions, observations


class DeepSeekToolsSystem(_DeepSeekToolSystem):
    name = "deepseek_tools"

    async def predict(self, alert: AlertEvent, case_id: str) -> dict:
        started = perf_counter()
        _plan, _results, executions, observations = await self._observe(alert, case_id)
        result = await self.client.diagnose(
            alert=self._public_alert(alert),
            tool_observations=observations,
        )
        return {
            "case_id": case_id,
            "status": "completed",
            "candidate_types": [item.value for item in result.decision.candidate_types],
            "evidence_types": result.decision.evidence_types,
            "tool_executions": [item.model_dump(mode="json") for item in executions],
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
        plan, results, executions, observations = await self._observe(alert, case_id)
        dimension_results = analyze_dimensions(alert, plan, results)
        expert_domains = next(
            (task.expert_domains for task in plan.dimensions if task.dimension == "downstream"),
            [],
        )
        expert_results = analyze_experts(alert, results, domains=expert_domains)
        evidence = collect_evidence(alert, results)
        evidence.extend(collect_semantic_evidence(alert, dimension_results))
        _signals, deterministic_evidence, _rules = run_deterministic_pipeline(
            alert, results, dimension_results, expert_results, evidence
        )
        evidence.extend(deterministic_evidence)
        evidence = sorted({item.evidence_id: item for item in evidence}.values(), key=lambda item: (-item.confidence, item.evidence_id))
        deterministic_candidates, _ = RootCauseAgent().diagnose(alert, evidence)
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
            "tool_executions": [item.model_dump(mode="json") for item in executions],
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
