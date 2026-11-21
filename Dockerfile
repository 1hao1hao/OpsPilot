# DeepRCA-Agent Service Dockerfile
# PRD-06 §3.1 — Agent 服务容器镜像

FROM python:3.11-slim AS base

# 系统依赖
WORKDIR /app

# 依赖层（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码层
COPY pyproject.toml .
COPY alembic.ini .
COPY migrations/ ./migrations/
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# 非 root 用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

CMD ["uvicorn", "opspilot.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
