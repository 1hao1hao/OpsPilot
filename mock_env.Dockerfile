# Mock Environment Service Dockerfile
# PRD-06 §3.2 — 模拟环境独立服务容器镜像

FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层
COPY requirements-mock.txt .
RUN pip install --no-cache-dir -r requirements-mock.txt

# 代码层（mock_env 模块依赖）
# 注: mock_routes.py 的 run_scenario_e2e 通过 HTTP API 调用 Agent，
#     不直接导入 deeprca.api/agents/graph/detection。
#     mock_env 仅依赖 config/（get_settings）。models/ 等目录由
#     pip install -e . 时 pyproject.toml 的 setuptools 自动解析。
COPY pyproject.toml .
COPY src/deeprca/__init__.py ./src/deeprca/__init__.py
COPY src/deeprca/config/ ./src/deeprca/config/
COPY src/deeprca/mock_env/ ./src/deeprca/mock_env/
COPY mock_main.py ./mock_main.py
RUN pip install --no-cache-dir -e .

# 非 root 用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8001/api/v1/mock/health || exit 1

CMD ["uvicorn", "mock_main:app", "--host", "0.0.0.0", "--port", "8001"]
