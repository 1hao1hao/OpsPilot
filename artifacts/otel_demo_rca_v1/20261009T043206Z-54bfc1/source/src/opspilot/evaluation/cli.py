"""Command line entry point for reproducible Stage-1 evaluations."""

from __future__ import annotations

import argparse
import asyncio

from opspilot.evaluation.concurrency import run_concurrency_benchmark
from opspilot.evaluation.reliability import run_reliability
from opspilot.evaluation.runner import run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(prog="opspilot-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--split", choices=["dev", "test"])
    reliability_parser = subparsers.add_parser("reliability")
    reliability_parser.add_argument("--config", required=True)
    concurrency_parser = subparsers.add_parser("concurrency")
    concurrency_parser.add_argument("--config", required=True)
    args = parser.parse_args()
    if args.command == "run":
        output = asyncio.run(run_evaluation(args.config, args.split))
        print(output)
    elif args.command == "reliability":
        output = asyncio.run(run_reliability(args.config))
        print(output)
    elif args.command == "concurrency":
        output = asyncio.run(run_concurrency_benchmark(args.config))
        print(output)


if __name__ == "__main__":
    main()
