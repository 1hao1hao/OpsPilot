#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON=${PYTHON:-python3}
# Native Windows Python emits CRLF when invoked from Git Bash.
readarray -t VERSION < <("$PYTHON" -c 'import json,sys; v=json.load(open(sys.argv[1])); print(v["repository"]); print(v["commit"]); print(v["demo_image_version"])' "$ROOT/integrations/opentelemetry_demo/version.json" | tr -d '\r')
DEMO_DIR=${OTEL_DEMO_DIR:-$ROOT/.external/opentelemetry-demo}
if [[ -n ${OTEL_DEMO_DIR:-} ]]; then
  # Explicit clones are read-only: never checkout/reset/fetch into somebody else's tree.
  [[ -d "$DEMO_DIR/.git" || -f "$DEMO_DIR/.git" ]] || { echo 'OTEL_DEMO_DIR must be an existing clone' >&2; exit 1; }
else
  if [[ ! -e "$DEMO_DIR" ]]; then
    mkdir -p "$(dirname "$DEMO_DIR")"
    git init "$DEMO_DIR" >&2
    git -C "$DEMO_DIR" remote add origin "${VERSION[0]}"
    git -C "$DEMO_DIR" fetch --depth 1 origin "${VERSION[1]}" >&2
    git -C "$DEMO_DIR" checkout --detach "${VERSION[1]}" >&2
  fi
fi
[[ $(git -C "$DEMO_DIR" rev-parse HEAD) == "${VERSION[1]}" ]] || { echo 'Demo commit mismatch; use a separate clone at version.json commit.' >&2; exit 1; }
[[ -z $(git -C "$DEMO_DIR" status --porcelain --untracked-files=no) ]] || { echo 'Demo tracked files are modified; refusing non-reproducible deployment.' >&2; exit 1; }
cd "$DEMO_DIR"
pwd
