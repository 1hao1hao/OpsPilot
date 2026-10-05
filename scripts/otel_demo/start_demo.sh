#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
"$ROOT/scripts/otel_demo/compose.sh" up -d --no-build --wait --wait-timeout 300
"${PYTHON:-python3}" "$ROOT/scripts/otel_demo/healthcheck.py"
