"""Metric formulas with explicit numerators and denominators."""

from __future__ import annotations

import math
from typing import Any

from opspilot.models import EvaluationCase, RootCauseType


def _ratio(numerator: float, denominator: int) -> dict[str, Any]:
    return {
        "value": round(float(numerator) / denominator, 6) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _p50(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return round(ordered[middle] if len(ordered) % 2 else
                 (ordered[middle - 1] + ordered[middle]) / 2, 3)


def compute_metrics(cases: list[EvaluationCase], predictions: list[dict]) -> dict[str, Any]:
    by_id = {prediction["case_id"]: prediction for prediction in predictions}
    fault_cases = [case for case in cases if case.is_fault]
    hit1 = 0
    hit3 = 0
    evidence_recalls: list[float] = []
    e2e = 0
    false_positives = 0
    normal_count = 0
    tool_success = 0
    tool_attempted = 0
    latencies: list[float] = []
    api_call_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    tool_calls = 0
    expert_calls = 0
    investigation_rounds = 0
    investigation_cases = 0
    planner_actions = 0
    valid_actions = 0
    duplicate_actions = 0
    budget_exhaustions = 0
    confirmed = 0
    confirmed_fault_cases = 0
    false_confirmations = 0
    inconclusive = 0
    requirements_satisfied = 0
    requirements_active = 0
    tasks_satisfied = 0
    tasks_active = 0
    expected_tools_selected = 0
    expected_tools_total = 0
    selected_tools_total = 0
    selected_tools_relevant = 0
    repeated_tool_targets = 0

    for case in cases:
        prediction = by_id.get(case.case_id, {"status": "missing", "candidate_types": []})
        candidates = prediction.get("candidate_types", [])
        if prediction.get("status") == "completed" and candidates:
            e2e += 1
        if case.is_fault:
            expected = case.expected_root_cause_type.value
            hit1 += bool(candidates and candidates[0] == expected)
            hit3 += expected in candidates[:3]
            expected_evidence = set(case.expected_evidence_types)
            actual_evidence = set(prediction.get("evidence_types", []))
            if expected_evidence:
                evidence_recalls.append(len(expected_evidence & actual_evidence) / len(expected_evidence))
        else:
            normal_count += 1
            false_positives += bool(candidates and candidates[0] != RootCauseType.NO_FAULT.value)
        for execution in prediction.get("tool_executions", []):
            tool_attempted += 1
            tool_calls += 1
            tool_success += execution.get("status") == "success"
        selected = {(item.get("tool_name"), item.get("target_service") or case.alert.service_name)
                    for item in prediction.get("tool_executions", [])}
        expected = set(case.expected_tools)
        selected_names = {name for name, _service in selected}
        expected_tools_selected += len(expected & selected_names)
        expected_tools_total += len(expected)
        selected_tools_relevant += len(expected & selected_names)
        selected_tools_total += len(selected_names)
        repeated_tool_targets += max(0, len(prediction.get("tool_executions", [])) - len(selected))
        investigation = prediction.get("investigation")
        if isinstance(investigation, dict):
            investigation_cases += 1
            expert_calls += int(investigation.get("expert_budget_used", 0))
            investigation_rounds += int(investigation.get("rounds", 0))
            duplicate_actions += int(investigation.get("duplicate_actions", 0))
            actions = investigation.get("action_history", [])
            planner_actions += len(actions)
            valid_actions += sum(
                action.get("action_type") in {"inspect_tool", "invoke_expert"}
                and bool(action.get("target"))
                for action in actions
            )
            budget_exhaustions += "budget exhausted" in str(investigation.get("stop_reason", ""))
            decision = investigation.get("decision") or {}
            verdict = decision.get("verdict")
            if verdict == "CONFIRMED":
                confirmed += 1
                confirmed_fault_cases += bool(case.is_fault)
                false_confirmations += not case.is_fault or not candidates or (
                    candidates[0] != case.expected_root_cause_type.value)
            inconclusive += verdict == "INCONCLUSIVE"
            for requirement in decision.get("requirements", []):
                if requirement.get("active", True):
                    requirements_active += 1
                    requirements_satisfied += bool(requirement.get("satisfied"))
        task_graph = prediction.get("task_graph")
        if isinstance(task_graph, dict):
            for task in task_graph.get("tasks", []):
                if task.get("status") != "CANCELLED":
                    tasks_active += 1
                    tasks_satisfied += task.get("status") == "SATISFIED"
        if isinstance(prediction.get("latency_ms"), (int, float)):
            latencies.append(float(prediction["latency_ms"]))
        model_usage = prediction.get("model_usage")
        usage = prediction.get("token_usage")
        if isinstance(model_usage, dict):
            api_call_count += int(model_usage.get("api_call_count", 0))
            prompt_tokens += int(model_usage.get("prompt_tokens", 0))
            completion_tokens += int(model_usage.get("completion_tokens", 0))
            total_tokens += int(model_usage.get("total_tokens", 0))
        elif isinstance(usage, dict):
            api_call_count += 1
            prompt_tokens += int(usage.get("prompt_tokens", 0))
            completion_tokens += int(usage.get("completion_tokens", 0))
            total_tokens += int(usage.get("total_tokens", 0))

    return {
        "case_count": len(cases),
        "root_cause_hit_at_1": _ratio(hit1, len(fault_cases)),
        "root_cause_hit_at_3": _ratio(hit3, len(fault_cases)),
        "evidence_recall_macro": {
            "value": round(sum(evidence_recalls) / len(evidence_recalls), 6) if evidence_recalls else None,
            "scored_cases": len(evidence_recalls),
        },
        "tool_success_rate": _ratio(tool_success, tool_attempted),
        "e2e_success_rate": _ratio(e2e, len(cases)),
        "false_positive_rate": _ratio(false_positives, normal_count),
        "p95_latency_ms": _p95(latencies),
        "p50_latency_ms": _p50(latencies),
        "confirmed_coverage": _ratio(confirmed_fault_cases, len(fault_cases)),
        "false_confirmation_rate": _ratio(false_confirmations, confirmed),
        "abstention_rate": _ratio(inconclusive, len(cases)),
        "requirement_coverage": _ratio(requirements_satisfied, requirements_active),
        "task_completion_rate": _ratio(tasks_satisfied, tasks_active),
        "tool_selection_recall": _ratio(expected_tools_selected, expected_tools_total),
        "tool_selection_precision": _ratio(selected_tools_relevant, selected_tools_total),
        "repeated_tool_targets": repeated_tool_targets,
        "average_tool_calls_per_case": round(tool_calls / len(cases), 6) if cases else None,
        "average_expert_calls_per_case": round(expert_calls / investigation_cases, 6) if investigation_cases else None,
        "average_investigation_rounds": round(investigation_rounds / investigation_cases, 6) if investigation_cases else None,
        "planner_action_valid_rate": _ratio(valid_actions, planner_actions),
        "duplicate_action_rate": _ratio(duplicate_actions, planner_actions),
        "budget_exhaustion_rate": _ratio(budget_exhaustions, investigation_cases),
        "latency_scope": "in-process end-to-end; Stage 1 has no queue time",
        "model_usage": {
            "api_call_count": api_call_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "average_total_tokens_per_call": (
                round(total_tokens / api_call_count, 3) if api_call_count else None
            ),
        },
    }
