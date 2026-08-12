"""健康检查冒烟测试 — PRD-06 §5.5 / §9。

验证 Agent 服务和 Mock 环境服务存活且健康端点可达。
"""

from __future__ import annotations


def test_agent_health(agent_client):
    """验证 Agent 服务健康。"""
    resp = agent_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"


def test_mock_health(mock_client):
    """验证 Mock 环境健康。"""
    resp = mock_client.get("/health")
    assert resp.status_code == 200


def test_mock_reset(mock_client):
    """验证 Mock 环境可重置。"""
    resp = mock_client.post("/reset")
    assert resp.status_code == 200


def test_mock_scenarios_list(mock_client):
    """验证 Mock 环境返回场景列表。"""
    resp = mock_client.get("/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 8  # PRD-05 §10 定义 8 个场景
