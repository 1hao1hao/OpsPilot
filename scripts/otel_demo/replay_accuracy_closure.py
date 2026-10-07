"""Development replay of historical observations, never a fresh accuracy claim."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from opspilot.evidence.temporal import temporal_payload
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.models import AlertEvent, ToolResult


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rehydrate", action="store_true", help="Recover temporal context from original raw measurements; no missing tools")
    args = parser.parse_args()
    records = []
    totals = defaultdict(lambda: {"fault_top1": 0, "fault_top3": 0, "faults": 0, "normal": 0, "normals": 0})
    for record in read(args.run / "records.json"):
        directory = Path(record["path"])
        event = AlertEvent.model_validate(read(directory / "alert.json"))
        results = [ToolResult.model_validate(r) for r in read(directory / "rca/tool_results.json")]
        if args.rehydrate:
            for result in results:
                observations = (result.data or {}).get("observations", {})
                for name, value in list(observations.items()):
                    if isinstance(value, dict) and value.get("data_points"):
                        points = [(p["timestamp"], p["value"]) for p in value["data_points"] if "timestamp" in p]
                        if points:
                            observations[name] = temporal_payload(points, event.timestamp.timestamp(), query=value.get("query"))
            for raw_path in (directory / "telemetry/raw").glob("*prometheus.json"):
                raw = read(raw_path)
                params = raw.get("arguments", {}).get("params", {})
                if not isinstance(params, dict) or raw.get("path") != "/api/v1/query_range":
                    continue
                query = params.get("query", "")
                mapping = (("kafka_consumer_group_lag", "kafka.lag", "consumer_lag"),
                           ("redis_keyspace_hits", "redis.hotkeys", "hit_rate_percent"))
                for token, tool, field in mapping:
                    if token not in query:
                        continue
                    points = [(t, v) for row in raw.get("response", {}).get("data", {}).get("result", []) for t, v in row.get("values", [])]
                    if points:
                        for result in results:
                            if result.tool_name == tool and result.data:
                                result.data["observations"][field] = temporal_payload(points, event.timestamp.timestamp(), query=query)
        analysis = DeterministicEvidenceEngine().analyze(event, results)
        original = read(directory / "rca/report.json")
        expected = record["score"]["expected"]
        candidates = [c.root_cause_type.value for c in analysis.candidates]
        values = totals[record["mode"]]
        if expected == "no_fault":
            values["normals"] += 1
            values["normal"] += candidates[0] == expected
        else:
            values["faults"] += 1
            values["fault_top1"] += candidates[0] == expected
            values["fault_top3"] += expected in candidates
        records.append({"scenario": record["scenario_id"], "repeat": record["repetition"], "mode": record["mode"],
                        "expected": expected, "before": original["primary_root_cause"]["root_cause_type"],
                        "after": candidates, "tools": [r.tool_name for r in results],
                        "evidence": [e.model_dump(mode="json") for e in analysis.evidence]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"kind": "historical replay; no new lifecycle or missing tool synthesis", "raw_context_rehydrated": args.rehydrate,
                                      "source": str(args.run), "totals": dict(totals), "records": records}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(totals)))


if __name__ == "__main__":
    main()
