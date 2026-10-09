import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[2] / "scripts/otel_demo/apply_tri_state_after_release.py"
spec = importlib.util.spec_from_file_location("deferred_apply", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(root, *arguments):
    return subprocess.run(["git", "-C", str(root), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pending(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "baseline")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    git(root, "config", "core.autocrlf", "false")
    (root / "src").mkdir()
    (root / "plan").mkdir()
    (root / ".external").mkdir()
    source = root / "src/example.py"
    source.write_text("frozen = True\n")
    original_sha = module.digest(source)
    document = root / "notes.md"
    document.write_text("original\n")
    git(root, "add", "src/example.py", "notes.md")
    git(root, "commit", "-m", "baseline")
    base = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-b", "tri-state")
    source.write_text("tri_state = True\n")
    plan = root / "plan/task_10.9.md"
    plan.write_text("task\ncompleted\n")
    git(root, "add", "src/example.py", "plan/task_10.9.md")
    git(root, "commit", "-m", "new implementation")
    target = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "baseline")
    plan.parent.mkdir(exist_ok=True)
    plan.write_text("task\n")
    plan_sha = module.digest(plan)
    document.write_text("user modifications\n")
    snapshot = root / "artifacts/otel_demo_rca_v1/top1_18_development/preserved_workspace.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps({"notes.md": module.digest(document)}))
    run = root / "release"
    run.mkdir()
    (run / "final_verification.json").write_text(json.dumps({"status": "passed", "actual_diagnoses": 63}))
    (run / "source_manifest.json").write_text(json.dumps({"src/example.py": original_sha}))
    return root, run, target, base, plan_sha


def test_apply_only_after_proof_and_preserve_uncommitted_user_work(pending):
    root, run, target, base, plan_sha = pending
    status = module.integrate(root, run, target, base, plan_sha)
    assert status["status"] == "completed"
    assert git(root, "rev-parse", "HEAD") == target
    assert (root / "notes.md").read_text() == "user modifications\n"
    assert (root / "plan/task_10.9.md").read_text() == "task\ncompleted\n"
    assert (root / ".external/task_10.9.original.md").read_text() == "task\n"
    assert status["new_experiments_started"] == 0


@pytest.mark.parametrize("change", ["source", "plan", "index", "lock"])
def test_changed_inputs_or_busy_workspace_abort_without_overwriting(pending, change):
    root, run, target, base, plan_sha = pending
    if change == "source":
        (root / "src/example.py").write_text("user edit\n")
    elif change == "plan":
        (root / "plan/task_10.9.md").write_text("changed task\n")
    elif change == "index":
        git(root, "add", "notes.md")
    else:
        (root / ".external/otel-benchmark.lock").write_text("running")
    with pytest.raises(AssertionError):
        module.integrate(root, run, target, base, plan_sha)
    assert git(root, "rev-parse", "HEAD") == base
    assert (root / "plan/task_10.9.md").exists()
    assert not (root / ".external/task_10.9.original.md").exists()
    assert (root / "notes.md").read_text() == "user modifications\n"
