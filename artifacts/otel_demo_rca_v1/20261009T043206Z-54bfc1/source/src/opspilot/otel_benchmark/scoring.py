"""Score only after RCA completes; never adjust candidates or gate decisions."""

from collections import defaultdict


def score_report(report, expected):
    candidates = [candidate["root_cause_type"] for candidate in report["candidates"]]
    trace = report["investigation"]
    gates = trace["gate_decisions"]
    return {
        "top1": bool(candidates) and candidates[0] == expected,
        "top3": expected in candidates[:3],
        "normal_correct": candidates[0] == "no_fault" if expected == "no_fault" else None,
        "gate_pass": bool(gates) and gates[-1]["sufficient"],
        "budget_exhausted": bool(gates) and gates[-1]["budget_exhausted"],
        "degraded": report["degraded"],
        "tool_calls": trace["tool_budget_used"], "expert_calls": trace["expert_budget_used"],
        "investigation_rounds": trace["rounds"], "latency_ms": report["latency_ms"],
        "expected": expected, "predicted": candidates[:3],
        "category_only": True,
    }


def summarize(records):
    groups = defaultdict(list)
    for record in records:
        groups[record["scenario_id"]].append(record)

    def aggregate(rows):
        scored = [r["score"] for r in rows if r.get("score") is not None]
        faults = [s for s in scored if s["normal_correct"] is None]
        normals = [s for s in scored if s["normal_correct"] is not None]
        verified_faults = [r["score"] for r in rows if r.get("score") is not None
                           and r["score"]["normal_correct"] is None and r.get("fault_observed") is True]

        def rate(values, field):
            return {"numerator": sum(bool(s[field]) for s in values), "denominator": len(values),
                    "rate": sum(bool(s[field]) for s in values) / len(values) if values else None}

        return {"attempts": len(rows), "scored": len(scored),
                "completed": sum(r["status"] == "completed" for r in rows),
                "fault_observation_confirmed": sum(r.get("fault_observed") is True for r in rows),
                "fault_observation_gap": sum(r.get("fault_observed") is False for r in rows),
                "top1": rate(faults, "top1"), "top3": rate(faults, "top3"),
                "verified_fault_top1": rate(verified_faults, "top1"),
                "verified_fault_top3": rate(verified_faults, "top3"),
                "no_fault_accuracy": rate(normals, "normal_correct"),
                "gate_pass_rate": rate(scored, "gate_pass"),
                "budget_exhausted_rate": rate(scored, "budget_exhausted"),
                "degraded_run_rate": rate(scored, "degraded")}

    return {"overall": aggregate(records), "by_scenario": {k: aggregate(v) for k, v in groups.items()}}
