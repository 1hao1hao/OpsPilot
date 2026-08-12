"""冒烟测试公共 fixture — PRD-06 §5.4。

为容器内冒烟测试提供 HTTP 客户端和服务地址配置。
通过环境变量 AGENT_URL / MOCK_URL 注入服务地址。
"""

from __future__ import annotations

import os

import httpx
import pytest

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8000")
MOCK_URL = os.getenv("MOCK_URL", "http://localhost:8001")


@pytest.fixture
def agent_client():
    """Agent 服务 HTTP 客户端。"""
    with httpx.Client(base_url=AGENT_URL, timeout=30) as client:
        yield client


@pytest.fixture
def mock_client():
    """Mock 环境 HTTP 客户端。"""
    base = MOCK_URL.rstrip("/") + "/api/v1/mock"
    with httpx.Client(base_url=base, timeout=10) as client:
        yield client


@pytest.fixture
def reset_mock(mock_client):
    """每个测试前重置模拟环境。"""
    mock_client.post("/reset")
    yield


@pytest.fixture
def run_scenario(mock_client):
    """执行预设场景并返回结果。"""
    def _run(scenario_name: str):
        resp = mock_client.post(f"/scenarios/{scenario_name}/run", timeout=120)
        return resp.json()
    return _run
