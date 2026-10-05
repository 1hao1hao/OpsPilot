#!/usr/bin/env python3
"""Record real Prometheus API discovery. Never write fixture data as live discovery."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from opspilot.observations.clients import PrometheusClient


async def run(args):
    timestamp = datetime.fromisoformat(args.timestamp) if args.timestamp else datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("--timestamp requires a timezone")
    if args.window <= 0:
        raise ValueError("--window must be positive")
    end = timestamp.timestamp()
    data = await PrometheusClient(args.url).discover(end - args.window, end, args.service)
    data["recorded_at"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Discovered {len(data['metric_names'])} metric names; artifact: {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("OPSPILOT_PROMETHEUS_URL"))
    parser.add_argument("--service", default="frontend")
    parser.add_argument("--timestamp", help="ISO timestamp; defaults to current UTC")
    parser.add_argument("--window", type=int, default=900)
    parser.add_argument("--output", type=Path, default=Path("artifacts/otel_demo/discovery/metrics.json"))
    args = parser.parse_args()
    if not args.url:
        parser.error("--url or OPSPILOT_PROMETHEUS_URL is required")
    asyncio.run(run(args))
