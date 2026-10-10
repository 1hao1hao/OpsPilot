"""Measured experiment adapters; the existing planner, analysis, ranker and gate are unchanged."""

import json
import time
import uuid
from datetime import UTC, datetime

from opspilot.agents import RootCauseAgent
from opspilot.graph import OpsPilotWorkflow
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.engine import InvestigationOutcome
from opspilot.investigation.planner import EvidenceGate, LLMAdaptivePlanner
from opspilot.investigation.report import build_report
from opspilot.llm import DeepSeekRCAClient, DeepSeekSecrets
from opspilot.models import InvestigationAction, InvestigationTrace, ToolCall
from opspilot.tools import ToolExecutor

from .isolation import LeakageGuard
from .runner import Capture, save

MODES = ("full_adaptive", "fixed_full", "adaptive_no_l2")


def mode_order(repetition):
    offset = (repetition - 1) % len(MODES)
    return MODES[offset:] + MODES[:offset]


class MeasuredClient(DeepSeekRCAClient):
    """Record actual API requests/responses without authorization headers or secrets."""

    def __init__(self, directory, guard, **kwargs):
        super().__init__(**kwargs)
        self.directory, self.guard = directory, guard
        self.calls = []

    async def _post(self, body):
        self.guard.assert_clean(body)
        number = len(self.calls) + 1
        record = {"call": number, "started_at": datetime.now(UTC).isoformat(), "request": body}
        self.calls.append(record)
        started = time.perf_counter()
        try:
            response = await super()._post(body)
            record.update(api_success=True, response=response, usage=response.get("usage"),
                          response_model=response.get("model"))
            return response
        except Exception as exc:
            record.update(api_success=False, error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            record["latency_ms"] = (time.perf_counter() - started) * 1000
            save(self.directory, f"llm/api-{number:02d}.json", record)

    async def complete_json(self, **kwargs):
        result = await super().complete_json(**kwargs)
        self.guard.assert_clean(result)
        return result


class MeasuredPlanner(LLMAdaptivePlanner):
    def __init__(self, registry, *, client, directory, guard):
        super().__init__(registry, llm_enabled=True, json_call=client.complete_json)
        self.decisions, self.directory, self.guard = [], directory, guard

    async def decide(self, **kwargs):
        action = await super().decide(**kwargs)
        decision = {"round": kwargs["round_number"], "llm_used": self.last_used_llm,
                    "fallback_reason": self.last_fallback_reason,
                    "action": action.model_dump(mode="json") if action else None}
        self.guard.assert_clean(decision)
        self.decisions.append(decision)
        save(self.directory, "llm/planner_decisions.json", self.decisions)
        return action


async def fixed_observe(registry, alert, settings):
    """All registry tools, measured executions, no invented Expert calls or LLM calls."""
    executor = ToolExecutor(registry)
    results, actions = [], []
    for name in registry.names():
        result = await executor.execute(ToolCall(tool_call_id=uuid.uuid4().hex, tool_name=name,
                                                arguments={"alert": alert.model_dump(mode="json")}))
        results.append(result)
        actions.append(InvestigationAction(action_type="inspect_tool", target=name, reason="Frozen full tool list",
                                           round=1, status="succeeded" if result.status.value == "success" else "failed"))
    analysis = DeterministicEvidenceEngine().analyze(alert, results)
    gate = EvidenceGate(confidence=settings.evidence_gate_confidence, margin=settings.evidence_gate_margin,
                        min_sources=settings.evidence_gate_min_sources).evaluate(analysis.candidates, analysis.evidence)
    trace = InvestigationTrace(rounds=1, action_history=actions, gate_decisions=[gate], executed_tools=registry.names(),
                               invoked_experts=[], stop_reason="Frozen full tool list completed",
                               tool_budget_used=len(results), expert_budget_used=0)
    return InvestigationOutcome(results, analysis.evidence, analysis.candidates, trace, {})


def llm_metrics(client, planner):
    calls = client.calls if client else []
    decisions = planner.decisions if planner else []
    usages = [call["usage"] for call in calls if isinstance(call.get("usage"), dict)]
    return {"api_calls": len(calls), "api_successes": sum(call["api_success"] for call in calls),
            "api_failures": sum(not call["api_success"] for call in calls),
            "planner_successes": sum(d["llm_used"] for d in decisions),
            "planner_fallbacks": sum(d["fallback_reason"] is not None for d in decisions),
            "llm_used": any(d["llm_used"] for d in decisions),
            "fallback_occurred": any(d["fallback_reason"] is not None for d in decisions),
            "usage_available_calls": len(usages),
            "tokens": {key: sum(u.get(key, 0) for u in usages) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}


async def run_mode(mode, directory, alert, settings, flag_names):
    guard = LeakageGuard(flag_names)
    capture = Capture(directory, settings, guard)
    registry = capture.registry()
    client = planner = None
    save(directory, "alert.json", alert.model_dump(mode="json"))
    save(directory, "config.json", {"mode": mode, "settings": settings.model_dump(mode="json"),
                                    "execution": "sequential", "fixed_tools": registry.names() if mode == "fixed_full" else None,
                                    "llm_role": "planner only; identical deterministic ranking/summary in all modes"})
    started = datetime.now(UTC)
    try:
        if mode == "fixed_full":
            outcome = await fixed_observe(registry, alert, settings)
        else:
            client = MeasuredClient(directory, guard, api_key=DeepSeekSecrets().deepseek_api_key,
                                    model=settings.llm_model, base_url=settings.llm_base_url,
                                    timeout_seconds=settings.llm_timeout_seconds, max_attempts=1, max_tokens=500)
            planner = MeasuredPlanner(registry, client=client, directory=directory, guard=guard)
            workflow = OpsPilotWorkflow(registry, root_cause_agent=RootCauseAgent(), settings=settings,
                                        execution_mode="sequential")
            workflow.investigator.planner = planner
            outcome = await workflow.observe(alert, trace_id=uuid.uuid4().hex)
        usage = llm_metrics(client, planner)
        rationale = RootCauseAgent().summarizer(outcome.provisional_candidates[0], outcome.evidence)
        report = build_report(alert=alert, outcome=outcome, trace_id=uuid.uuid4().hex, started_at=started,
                              rationale=rationale, llm_used=usage["llm_used"]).model_dump(mode="json")
        guard.assert_clean(report)
        save(directory, "rca/report.json", report)
        save(directory, "rca/events.json", outcome.trace.model_dump(mode="json"))
        save(directory, "rca/tool_results.json", [r.model_dump(mode="json") for r in outcome.tool_results])
        save(directory, "rca/evidence.json", [r.model_dump(mode="json") for r in outcome.evidence])
        save(directory, "llm/metrics.json", usage)
        print(f"MODE {mode}: candidates={[c.root_cause_type.value for c in outcome.provisional_candidates]}, "
              f"tools={outcome.trace.tool_budget_used}, experts={outcome.trace.expert_budget_used}, llm={usage}", flush=True)
        return report, outcome
    finally:
        capture.flush()
        if client:
            # Persist partial usage even if a mode fails after an API request.
            save(directory, "llm/metrics.json", llm_metrics(client, planner))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
