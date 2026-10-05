"""Deployment safety contracts without a Docker daemon or a network clone."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def deployment(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts/otel_demo"
    shutil.copytree(ROOT / "scripts/otel_demo", scripts)
    integration = project / "integrations/opentelemetry_demo"
    integration.mkdir(parents=True)
    clone = tmp_path / "existing demo"
    clone.mkdir()
    for name in ("compose.yaml", "compose.full.yaml", "compose.observability.yaml"):
        (clone / name).write_text("services: {}\n")
    (clone / ".env").write_text("DEMO_VERSION=latest\n")
    (clone / "flags.json").write_text("{}\n")
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True).strip()
    version = {"repository": str(clone), "commit": commit, "demo_image_version": "3.1.0"}
    (integration / "version.json").write_text(json.dumps(version))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(
        f'#!{Path(sys.executable).as_posix()}\nimport json,os,sys\n'
        'print(json.dumps({"argv":sys.argv[1:],"version":os.environ["DEMO_VERSION"]}))\n',
        newline="\n",
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "OTEL_DEMO_DIR": clone.as_posix(),
        "PYTHON": Path(sys.executable).as_posix(),
    }
    return scripts, clone, integration, version, env


def run_script(deployment, name, *args):
    scripts, clone, _, _, env = deployment
    bash = shutil.which("bash") or "bash"
    if os.name == "nt" and (git := shutil.which("git")):
        git_bash = Path(git).parent.parent / "bin/bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    return subprocess.run(
        [bash, str(scripts / name), *args],
        env=env,
        cwd=clone.parent,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_existing_clone_pin_and_relative_path(deployment):
    _, clone, _, _, env = deployment
    env["OTEL_DEMO_DIR"] = clone.name
    result = run_script(deployment, "bootstrap_demo.sh")
    assert result.returncode == 0, result.stderr
    reported = result.stdout.strip()
    if os.name == "nt" and len(reported) > 2 and reported[0] == "/" and reported[2] == "/":
        reported = f"{reported[1]}:{reported[2:]}"
    assert Path(reported) == clone


def test_wrong_pin_rejected_without_mutating_existing_clone(deployment):
    _, clone, integration, version, _ = deployment
    actual = version["commit"]
    version["commit"] = "0" * 40
    (integration / "version.json").write_text(json.dumps(version))
    result = run_script(deployment, "bootstrap_demo.sh")
    assert result.returncode != 0
    assert "commit mismatch" in result.stderr
    assert subprocess.check_output(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True).strip() == actual


def test_dirty_flags_block_start_but_allow_stop(deployment):
    _, clone, _, _, _ = deployment
    (clone / "flags.json").write_text('{"changed":true}\n')
    assert run_script(deployment, "bootstrap_demo.sh").returncode != 0
    result = run_script(deployment, "stop_demo.sh")
    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command["argv"][-1] == "down"
    assert command["version"] == "3.1.0"
    assert (clone / "flags.json").read_text() == '{"changed":true}\n'


def test_compose_pins_images_and_all_layers(deployment):
    result = run_script(deployment, "compose.sh", "config")
    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command["version"] == "3.1.0"
    assert command["argv"].count("-f") == 4
    assert "opspilot-otel-demo" in command["argv"]
