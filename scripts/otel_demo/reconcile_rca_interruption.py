"""Correct the interruption index against authoritative saved lifecycle artifacts."""

import argparse
import json
from pathlib import Path

from opspilot.otel_benchmark.modes import read_json


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    status = read_json(run / "status.json")
    assert status["status"] == "completed", "Reconcile only after the controller exits"
    history = run / "resume_history/process_exit_01"
    if (history / "index_correction.json").exists():
        return
    records, pairs = read_json(run / "records.json"), read_json(run / "pairs.json")
    complete_paths = {r["path"] for r in records if r["status"] == "completed"}
    duplicate_records = [r for r in records if r["status"] == "process_terminated" and r["path"] in complete_paths]
    assert len(duplicate_records) == 3
    first = next(p for p in pairs if p["scenario_id"] == "compute" and p["repetition"] == 1 and p["status"] == "completed")
    assert first["symptom_recovery_confirmed"] and first["recovery_health"] and first["recovery_queries_success"]
    interrupted = run / "compute/repeat-02"
    assert interrupted.exists() and not list(interrupted.glob("modes/*/benchmark/score.json"))
    write(history / "records_before_index_correction.json", records)
    write(history / "pairs_before_index_correction.json", pairs)
    write(history / "status_before_index_correction.json", status)
    corrected = [r for r in records if r not in duplicate_records]
    for p in pairs:
        if p["status"] == "process_terminated":
            p.update(repetition=2, path=str(interrupted), recovery_health=None,
                     recovery_queries_success=None, symptom_recovery_confirmed=None,
                     note="Process exited after compute #1 completed and during #2. No RCA score in #2; original telemetry retained. Recovery state unconfirmed at exit. Logs had lagged authoritative JSON; duplicate index corrected without counting new diagnoses.")
    write(run / "records.json", corrected)
    write(run / "pairs.json", pairs)
    status.update(actual_diagnoses=len(corrected), actual_lifecycles=len(pairs))
    write(run / "status.json", status)
    write(history / "index_correction.json", {"removed_duplicate_index_entries": len(duplicate_records),
          "actual_interrupted_scenario": "compute", "actual_interrupted_repetition": 2,
          "interrupted_diagnoses": 0, "actual_saved_diagnoses": len(corrected),
          "source_of_truth": "completed lifecycle JSON, recovery/final_checks.json and actual per-mode score files",
          "original_metadata_and_all_telemetry_preserved": True})
    print("Interruption index corrected against actual saved artifacts; duplicate entries do not count as diagnoses")


if __name__ == "__main__":
    main()
