# Smoke Test Container Dockerfile
# PRD-06 §3.3 — 冒烟测试执行器（run-to-completion）

FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

# 代码层（需要 src 以支持冒烟测试中的图导入）
COPY pyproject.toml .
COPY src/ ./src/
COPY tests/ ./tests/
RUN pip install --no-cache-dir -e .

# 入口脚本
COPY tests/smoke/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=/app/src

ENTRYPOINT ["/entrypoint.sh"]
CMD ["pytest", "tests/smoke/", "-v", "--tb=short"]
