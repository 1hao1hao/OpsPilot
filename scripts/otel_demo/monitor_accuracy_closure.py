"""Print compact, read-only live progress from authoritative lifecycle artifacts."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    pairs = read(args.run / "pairs.json", [])
    records = read(args.run / "records.json", [])
    latest = max(args.run.glob("*/repeat-*/scenario.json"), key=lambda p: p.stat().st_mtime, default=None)
    score_files = list(args.run.glob("*/repeat-*/modes/*/benchmark/score.json"))
    scores = {}
    for path in score_files:
        mode = path.parents[1].name
        value = read(path, {})
        if not value:
            continue
        mode_scores = scores.setdefault(mode, {"fault_top1": 0, "faults_scored": 0, "normal_correct": 0, "normals_scored": 0})
        if value["normal_correct"] is None:
            mode_scores["faults_scored"] += 1
            mode_scores["fault_top1"] += value["top1"]
        else:
            mode_scores["normals_scored"] += 1
            mode_scores["normal_correct"] += value["normal_correct"]
    print(json.dumps({"at": datetime.now(UTC).isoformat(), "status": read(args.run / "status.json", {}),
                      "indexed_lifecycles": len(pairs), "indexed_diagnoses": len(records),
                      "current": str(latest.parent.relative_to(args.run)) if latest else None,
                      "written_scores_not_final_acceptance": scores,
                      "final_verified": (args.run / "final_verification.json").exists()}))


if __name__ == "__main__":
    main()
