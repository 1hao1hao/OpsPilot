#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -c 'import alembic, asyncpg, fastapi, opspilot, redis, sqlalchemy'
printf '%s\n' "Environment ready: $project_root/.venv"
printf '%s\n' "Activate with: source .venv/bin/activate"
