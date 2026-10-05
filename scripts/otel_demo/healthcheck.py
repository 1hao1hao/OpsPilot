#!/usr/bin/env python3
"""Read-only health checks for the separate Astronomy Shop deployment."""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check():
    ports = {
        "frontend": ("FRONTEND", 18080, "/"),
        "load-generator": ("LOCUST", 18089, "/stats/requests"),
        "prometheus": ("PROMETHEUS", 19090, "/api/v1/query?query=up"),
        "jaeger": ("JAEGER", 16686, "/jaeger/ui/api/v3/services"),
        "opensearch": ("OPENSEARCH", 19200, "/_cluster/health"),
        "flagd": ("FLAGD_HEALTH", 18014, "/readyz"),
    }
    results = {}
    bash = shutil.which("bash") or "bash"
    if os.name == "nt" and (git := shutil.which("git")):
        git_bash = Path(git).parent.parent / "bin/bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    for name, (env, port, path) in ports.items():
        url = f"http://127.0.0.1:{os.getenv('OTEL_DEMO_' + env + '_PORT', str(port))}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read()
            if name != "frontend" and name != "flagd":
                data = json.loads(body)
                if name == "load-generator" and data.get("state") not in {"running", "spawning"}:
                    raise ValueError("load generator is not running")
                if name == "prometheus" and data.get("status") != "success":
                    raise ValueError("Prometheus query unsuccessful")
                if name == "jaeger" and not isinstance(data.get("services"), list):
                    raise ValueError("Jaeger query unsuccessful")
                if name == "opensearch" and data.get("status") not in {"green", "yellow"}:
                    raise ValueError("OpenSearch unhealthy")
            results[name] = {"status": "PASS", "url": url}
        except (OSError, ValueError) as exc:
            results[name] = {"status": "FAIL", "reason": str(exc)}
    for name, command in {
        "kafka": ["nc", "-z", "kafka", "9092"],
        "astronomy-db": ["pg_isready", "-U", "postgres"],
        "valkey-cart": ["valkey-cli", "ping"],
    }.items():
        try:
            subprocess.run(
                [bash, str(ROOT / "scripts/otel_demo/compose.sh"), "exec", "-T", name, *command],
                check=True,
                timeout=30,
                capture_output=True,
            )
            results[name] = {"status": "PASS"}
        except (OSError, subprocess.SubprocessError) as exc:
            results[name] = {"status": "FAIL", "reason": str(exc)}
    output = ROOT / "artifacts/otel_demo/healthcheck.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return all(r["status"] == "PASS" for r in results.values())


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
