#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
case "${1:-}" in
  down|stop|logs|ps|exec)
    # Inspect/stop an existing deployment even after flagd-ui changed its flag file.
    # These commands must never bootstrap a new clone or require a pristine worktree.
    DEMO_DIR=${OTEL_DEMO_DIR:-$ROOT/.external/opentelemetry-demo}
    [[ -f "$DEMO_DIR/compose.yaml" ]] || { echo 'No existing demo deployment directory' >&2; exit 1; }
    DEMO_DIR=$(cd "$DEMO_DIR" && pwd)
    ;;
  *) DEMO_DIR=$("$ROOT/scripts/otel_demo/bootstrap_demo.sh") ;;
esac
export DEMO_VERSION
DEMO_VERSION=$("${PYTHON:-python3}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["demo_image_version"])' "$ROOT/integrations/opentelemetry_demo/version.json" | tr -d '\r')
# Always resolve upstream relative paths/env from the verified clone.
exec docker compose --project-directory "$DEMO_DIR" --env-file "$DEMO_DIR/.env" \
  -p opspilot-otel-demo -f "$DEMO_DIR/compose.yaml" -f "$DEMO_DIR/compose.full.yaml" \
  -f "$DEMO_DIR/compose.observability.yaml" -f "$ROOT/integrations/opentelemetry_demo/compose.override.yaml" "$@"
