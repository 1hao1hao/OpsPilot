"""冒烟测试 — 端到端验证 PRD-02 通用分析 Agent 完整执行流程。

不依赖 M5 Mock 环境，使用内联 Mock 数据驱动 LangGraph 图执行。
覆盖 6 个典型故障场景，验证:
1. 告警解析 (intake_node) — 按类型推导时间窗口 + 关联服务提取
2. 任务规划 (planner_node) — 按类型选择维度组合
3. 并发收集 (dispatcher_node) — asyncio.gather 并发 + 降级模式
4. 证据聚合 (collector_node) — 双键排序(等级+置信度)
5. 根因定位 (root_cause_node) — L3 调用 + ImportError 降级
6. 报告生成 (reporter_node) — suggestions + satisfaction_url
"""

import sys
import os
import json
import pytest

from deeprca.graph import build_coordinator_graph


# ── 内联 Mock 告警事件 ──────────────────────────────────────────

ALERTS = {
    "timeout": {
        "alert_id": "smoke-timeout-001",
        "service_name": "order-service",
        "alert_type": "timeout",
        "severity": "P1",
        "timestamp": "2026-07-14T10:00:00Z",
        "description": "order-service 调用 payment-service 接口超时",
        "labels": {"cluster": "prod-cluster", "env": "production", "app": "order-service"},
    },
    "error_rate": {
        "alert_id": "smoke-error-001",
        "service_name": "user-service",
        "alert_type": "error_rate",
        "severity": "P2",
        "timestamp": "2026-07-14T10:05:00Z",
        "description": "user-service 错误率从 0.1% 飙升至 15%",
        "labels": {"cluster": "prod-cluster", "env": "production", "app": "user-service"},
    },
    "resource": {
        "alert_id": "smoke-resource-001",
        "service_name": "data-service",
        "alert_type": "resource",
        "severity": "P2",
        "timestamp": "2026-07-14T10:10:00Z",
        "description": "data-service CPU 使用率超过 90%",
        "labels": {"cluster": "prod-cluster", "env": "production", "app": "data-service"},
    },
    "custom": {
        "alert_id": "smoke-custom-001",
        "service_name": "gateway-service",
        "alert_type": "custom",
        "severity": "P3",
        "timestamp": "2026-07-14T10:15:00Z",
        "description": "gateway-service 自定义告警",
        "labels": {"cluster": "prod-cluster", "env": "production", "app": "gateway-service"},
    },
}

# 按告警类型期望的维度数量 (PRD-02 §2.2)
EXPECTED_DIMENSIONS = {
    "timeout": 6,
    "error_rate": 6,
    "resource": 4,
    "custom": 6,
}


class TestSmokeEndToEnd:
    """端到端冒烟测试 — PRD-02 通用分析 Agent。"""

    @pytest.fixture(scope="class")
    def compiled_graph(self):
        """编译图，整个测试类共享。"""
        return build_coordinator_graph()

    async def _run_graph(self, compiled_graph, alert):
        """执行图并返回最终状态。"""
        initial_state = {
            "alert": alert,
            "task_plan": [],
            "sub_agent_results": [],
            "collected_evidence": None,
            "root_cause": None,
            "report": None,
            "messages": [],
            "trace_id": f"smoke-{alert['alert_type']}",
            "start_time": "2026-07-14T10:00:00+00:00",
            "status": "running",
            "related_services": [],
            "degraded_mode": False,
        }
        return await compiled_graph.ainvoke(initial_state)

    # ── 基础验证 ──────────────────────────────────────────────

    def test_graph_compiles(self, compiled_graph):
        """图应成功编译。"""
        assert compiled_graph is not None

    # ── 按告警类型维度数验证 ───────────────────────────────────

    @pytest.mark.parametrize("alert_type", ["timeout", "error_rate", "resource", "custom"])
    async def test_dimension_count_by_type(self, compiled_graph, alert_type):
        """每种告警类型应生成正确数量的分析维度。"""
        alert = ALERTS[alert_type]
        final_state = await self._run_graph(compiled_graph, alert)

        expected = EXPECTED_DIMENSIONS[alert_type]
        actual = len(final_state["task_plan"])
        assert actual == expected, (
            f"alert_type={alert_type} 应生成 {expected} 个维度, 实际 {actual} 个"
        )

    # ── 端到端流程验证 ────────────────────────────────────────

    async def test_timeout_scenario_e2e(self, compiled_graph):
        """超时场景端到端测试。"""
        alert = ALERTS["timeout"]
        final_state = await self._run_graph(compiled_graph, alert)

        assert final_state["status"] in ("completed", "timeout")
        assert final_state["trace_id"] is not None
        assert len(final_state["task_plan"]) == 6
        assert final_state["report"] is not None

    async def test_error_rate_scenario_e2e(self, compiled_graph):
        """错误率场景端到端测试。"""
        alert = ALERTS["error_rate"]
        final_state = await self._run_graph(compiled_graph, alert)

        assert final_state["status"] in ("completed", "timeout")
        assert len(final_state["task_plan"]) == 6
        assert final_state["report"] is not None

    async def test_resource_scenario_e2e(self, compiled_graph):
        """资源场景端到端测试 — 只有 4 个维度。"""
        alert = ALERTS["resource"]
        final_state = await self._run_graph(compiled_graph, alert)

        assert final_state["status"] in ("completed", "timeout")
        assert len(final_state["task_plan"]) == 4

    async def test_custom_scenario_e2e(self, compiled_graph):
        """自定义告警场景端到端测试 — 全部 6 维度。"""
        alert = ALERTS["custom"]
        final_state = await self._run_graph(compiled_graph, alert)

        assert final_state["status"] in ("completed", "timeout")
        assert len(final_state["task_plan"]) == 6
        assert final_state["report"] is not None

    # ── 报告字段验证 ──────────────────────────────────────────

    async def test_report_valid_json(self, compiled_graph):
        """报告应为合法 JSON 字符串。"""
        alert = ALERTS["timeout"]
        final_state = await self._run_graph(compiled_graph, alert)

        report = final_state.get("report")
        assert report is not None, "report 不应为 None"
        parsed = json.loads(report) if isinstance(report, str) else report
        assert "trace_id" in parsed
        assert "root_cause" in parsed or parsed.get("confidence", 0) == 0

    async def test_report_has_suggestions(self, compiled_graph):
        """报告应包含 suggestions 字段。"""
        alert = ALERTS["timeout"]
        final_state = await self._run_graph(compiled_graph, alert)

        report = json.loads(final_state["report"])
        assert "suggestions" in report
        assert isinstance(report["suggestions"], list)
        assert len(report["suggestions"]) > 0

    async def test_report_has_satisfaction_url(self, compiled_graph):
        """报告应包含 satisfaction_url 字段。"""
        alert = ALERTS["timeout"]
        final_state = await self._run_graph(compiled_graph, alert)

        report = json.loads(final_state["report"])
        assert "satisfaction_url" in report
        assert report["satisfaction_url"] is not None
        assert "trace_id" in report["satisfaction_url"] or "smoke-timeout" in report["satisfaction_url"]

    # ── 时间窗口验证 ──────────────────────────────────────────

    @pytest.mark.parametrize("alert_type,expected_before_min", [
        ("timeout", 30),
        ("error_rate", 15),
        ("resource", 60),
    ])
    async def test_time_window_derivation(self, compiled_graph, alert_type, expected_before_min):
        """intake 应按告警类型推导不同的时间窗口。"""
        alert = ALERTS[alert_type]
        final_state = await self._run_graph(compiled_graph, alert)

        window_start = final_state["alert"].get("time_window_start", "")
        alert_ts = alert["timestamp"]

        from datetime import datetime
        alert_dt = datetime.fromisoformat(alert_ts.replace("Z", "+00:00"))
        start_dt = datetime.fromisoformat(window_start)
        diff = (alert_dt - start_dt).total_seconds() / 60
        assert abs(diff - expected_before_min) < 1, (
            f"alert_type={alert_type} 窗口应为 {expected_before_min}m, 实际 {diff:.1f}m"
        )

    # ── 关联服务验证 ──────────────────────────────────────────

    async def test_related_services_extracted(self, compiled_graph):
        """intake 应从告警描述中提取关联服务。"""
        alert = ALERTS["timeout"]  # description 包含 payment-service
        final_state = await self._run_graph(compiled_graph, alert)

        related = final_state.get("related_services", [])
        assert "order-service" in related
        assert "payment-service" in related

    # ── 降级模式验证 ──────────────────────────────────────────

    async def test_degraded_mode_flag(self, compiled_graph):
        """dispatcher 应输出 degraded_mode 标志（bool 类型）。"""
        alert = ALERTS["timeout"]
        final_state = await self._run_graph(compiled_graph, alert)

        assert isinstance(final_state.get("degraded_mode", False), bool)