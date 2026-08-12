#!/bin/bash
# tests/smoke/entrypoint.sh — PRD-06 §5.3
# 等待 Agent 和 Mock 服务就绪后执行测试

set -e

AGENT_URL="${AGENT_URL:-http://deeprca-agent:8000}"
MOCK_URL="${MOCK_URL:-http://mock-env:8001}"
MAX_RETRIES=30
RETRY_INTERVAL=2

echo "[smoke-test] Waiting for services to be ready..."

wait_for_service() {
    local url=$1
    local name=$2
    local retries=0
    while [ $retries -lt $MAX_RETRIES ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo "[smoke-test] $name is ready ($url)"
            return 0
        fi
        retries=$((retries + 1))
        sleep $RETRY_INTERVAL
    done
    echo "[smoke-test] ERROR: $name not ready after $MAX_RETRIES retries"
    return 1
}

wait_for_service "$AGENT_URL/health" "Agent"
wait_for_service "$MOCK_URL/api/v1/mock/health" "Mock Env"

echo "[smoke-test] All services ready, running tests..."
exec "$@"
