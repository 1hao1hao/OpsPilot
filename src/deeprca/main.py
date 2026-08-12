"""FastAPI 应用入口 — REST 端点 + WebSocket。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：FastAPI 骨架 + 5 端点 + WebSocket</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.5</td></tr>
<tr><td>0.2.0</td><td>移除 inline placeholder，接线到 routes.py create_router()</td><td>REQ: 20260713-API 接线</td></tr>
<tr><td>0.3.1</td><td>添加模块级 app 变量，修复 Dockerfile CMD 启动失败</td><td>REQ: 20260722-全流程修复</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import sys
from importlib.metadata import version as pkg_version

import httpx
from fastapi import FastAPI

from deeprca.api.routes import create_router
from deeprca.config import get_settings
from deeprca.mock_env import create_mock_router

__all__ = ["create_app", "app"]

# 从 pyproject.toml [project].version 动态读取，避免手动同步
try:
    _VERSION = pkg_version("deeprca-agent")
except Exception:
    _VERSION = "0.3.0"  # fallback（与 pyproject.toml 保持同步）

# 模块级 Redis 连接（供 /health 复用，避免每次请求新建连接）
_health_redis = None


async def _get_health_redis():
    """获取或创建 /health 专用的 Redis 连接（模块级单例）。"""
    global _health_redis
    if _health_redis is None:
        import redis.asyncio as aioredis
        settings = get_settings()
        _health_redis = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
        )
    return _health_redis


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    settings = get_settings()

    app = FastAPI(
        title="DeepRCA-Agent",
        description="LLM Agent 驱动的故障诊断智能体系统",
        version=_VERSION,
    )

    # --- Health Check (PRD-06 §9.1: 检查 Redis + Mock 连通性) --- #
    @app.get("/health")
    async def health_check():
        checks = {}

        # Redis 连通性（复用模块级连接，避免每次请求新建）
        # 增加一次重试，避免短暂网络抖动导致误报不健康
        for attempt in range(2):
            try:
                r = await _get_health_redis()
                await r.ping()
                checks["redis"] = "healthy"
                break
            except Exception:
                if attempt == 0:
                    # 连接可能已失效，重置以便下次重建后重试
                    global _health_redis
                    _health_redis = None
                    continue
                checks["redis"] = "unhealthy"

        # Mock 环境连通性
        try:
            async with httpx.AsyncClient() as client:
                mock_url = settings.mock_k8s_api.rstrip("/")
                resp = await client.get(f"{mock_url}/api/v1/mock/health", timeout=3)
                checks["mock_env"] = "healthy" if resp.status_code == 200 else "unhealthy"
        except Exception:
            checks["mock_env"] = "unhealthy"

        all_healthy = all(v == "healthy" for v in checks.values())
        return {
            "status": "healthy" if all_healthy else "degraded",
            "version": _VERSION,
            "env": settings.app_env,
            "checks": checks,
        }

    # --- API v1 路由 --- #
    app.include_router(create_router())

    # --- Mock 环境路由 --- #
    app.include_router(create_mock_router())

    return app


# 模块级 app 实例 — 供 uvicorn deeprca.main:app 使用
app = create_app()


# Windows EventLoop 兼容 (RISK-DA002 用户决策: 方案1)
if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())