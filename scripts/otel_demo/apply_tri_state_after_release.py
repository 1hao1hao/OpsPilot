"""Apply tested code only after an existing frozen release finishes its audit.

Never starts experiments. Refuses changed source, branch/index conflicts and
unexpected untracked collisions; preserves unrelated workspace edits.
"""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def git(root, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)
    return result.stdout.decode("utf-8").strip()


def integrate(root, run, target, original_head, plan_sha):
    proof = json.loads((run / "final_verification.json").read_text(encoding="utf-8"))
    assert proof["status"] == "passed" and proof["actual_diagnoses"] == 63
    assert not (root / ".external/otel-benchmark.lock").exists()
    assert git(root, "rev-parse", "HEAD") == original_head, "Branch changed; preserving workspace"
    assert not git(root, "diff", "--cached", "--name-only"), "User staged changes; preserving index"
    manifest = json.loads((run / "source_manifest.json").read_text(encoding="utf-8"))
    assert all(digest(root / name) == expected for name, expected in manifest.items()), "Frozen inputs changed"
    preservation = json.loads((root / "artifacts/otel_demo_rca_v1/top1_18_development/preserved_workspace.json").read_text())
    before = {name: digest(root / name) for name in preservation}
    plan = root / "plan/task_10.9.md"
    assert digest(plan) == plan_sha, "Task plan changed; preserving user edits"
    changed = git(root, "diff", "--name-only", original_head, target).splitlines()
    tracked = set(git(root, "ls-files").splitlines())
    assert all(name in tracked or not (root / name).exists() or name == "plan/task_10.9.md"
               for name in changed), "Unexpected untracked collision; preserving files"
    backup = root / ".external/task_10.9.original.md"
    assert not backup.exists(), "Plan backup already exists"
    plan.rename(backup)
    try:
        git(root, "merge", "--ff-only", target)
    except Exception:
        if not plan.exists():
            backup.rename(plan)
        raise
    assert before == {name: digest(root / name) for name in preservation}, "Unrelated edits changed"
    return {"status": "completed", "completed_at": datetime.now(UTC).isoformat(),
            "commit": git(root, "rev-parse", "HEAD"), "frozen_release": proof,
            "unrelated_edits_preserved": True, "original_plan_backup": str(backup),
            "new_experiments_started": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--original-head", required=True)
    parser.add_argument("--plan-sha", required=True)
    args = parser.parse_args()
    root, run = args.root.resolve(), args.run.resolve()
    state = root / "artifacts/tri_state_apply_status.json"
    state.write_text(json.dumps({"status": "waiting_for_existing_release", "target": args.target,
                                 "new_experiments_started": 0}, indent=2), encoding="utf-8")
    try:
        finished_wait_started = None
        while True:
            release = json.loads((run / "status.json").read_text(encoding="utf-8"))
            if release["status"] not in {"running", "completed"}:
                raise RuntimeError("Existing release interrupted; no merge or new experiment")
            if release["status"] == "completed":
                finished_wait_started = finished_wait_started or time.monotonic()
                errors = root / "artifacts/otel_demo_rca_v1/top1_18_development/background_finalize_error.log"
                if errors.exists() and errors.stat().st_size:
                    raise RuntimeError("Release finalizer failed; see background_finalize_error.log; no merge")
                if time.monotonic() - finished_wait_started > 600:
                    raise RuntimeError("Release audit did not finish within ten minutes; no merge")
            final = run / "final_verification.json"
            report = root / "reports/top1_18.md"
            readme = root / "README.md"
            marker = f"## Incident-local RCA validation ({run.name})"
            if (release["status"] == "completed" and final.exists() and report.exists()
                    and marker in readme.read_text(encoding="utf-8")
                    and not (root / ".external/otel-benchmark.lock").exists()):
                break
            time.sleep(30)
        result = integrate(root, run, args.target, args.original_head, args.plan_sha)
    except Exception as exc:  # noqa: BLE001 - persist unattended failures without overwriting files
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                  "new_experiments_started": 0}
    state.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
