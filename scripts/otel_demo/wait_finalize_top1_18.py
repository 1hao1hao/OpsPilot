"""Finalize the already-running frozen release once; never start an experiment."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    run = args.run.resolve()
    while True:
        status = json.loads((run / "status.json").read_text(encoding="utf-8"))
        if status["status"] != "running":
            break
        time.sleep(30)
    while (root / ".external/otel-benchmark.lock").exists():
        time.sleep(5)
    if status["status"] != "completed":
        raise SystemExit("Existing release interrupted; retained as-is. No new experiment started.")
    subprocess.run([sys.executable, str(root / "scripts/otel_demo/finalize_top1_18.py"), str(run)],
                   cwd=root, check=True)


if __name__ == "__main__":
    main()
