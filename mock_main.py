"""Mock 环境独立服务入口 — PRD-06 §3.2。

当作为独立容器运行时，仅加载 Mock 路由，不加载 Agent 分析图。
端口 8001，健康检查端点 /api/v1/mock/health。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：独立 Mock 服务入口</td><td>PRD-06 §3.2</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from fastapi import FastAPI

from deeprca.mock_env import create_mock_router

__all__ = ["create_mock_app"]


def create_mock_app() -> FastAPI:
    """创建 Mock 环境独立 FastAPI 应用。"""
    app = FastAPI(
        title="DeepRCA-MockEnv",
        description="DeepRCA 模拟环境服务（独立部署）",
        version="0.1.0",
    )

    # Mock 路由已包含 /api/v1/mock 前缀
    app.include_router(create_mock_router())

    return app


app = create_mock_app()
