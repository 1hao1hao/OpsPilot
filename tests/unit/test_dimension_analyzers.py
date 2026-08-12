"""PRD-03 L1 六维度分析器单元测试。

覆盖 §7 变更分析 Agent 和 §8 错误日志分析 Agent，
以及 upstream/downstream/cluster/problem 维度。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.3.0</td><td>初始创建：L1 维度分析器测试</td><td>REQ: PRD-03 §7, §8</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from deeprca.agents.dimensions import (
    analyze_change,
    analyze_cluster,
    analyze_downstream,
    analyze_errorlog,
    analyze_problem,
    analyze_upstream,
)
from deeprca.models import SubAgentResult


# ─────────────────────────────────────────────
# 变更分析 Agent (§7)
# ─────────────────────────────────────────────

class TestChangeAnalyzer:
    """PRD-03 §7: 变更在告警前 20 分钟 → 置信度 ≥ 0.7。"""

    @pytest.mark.asyncio
    async def test_recent_change_high_confidence(self):
        """1 条变更记录 → 置信度 ≥ 0.7。"""
        mock_result = {
            "changes": [
                {
                    "type": "deployment",
                    "timestamp": "2026-07-10T13:40:00Z",
                    "description": "发布新版本 v2.3.0",
                    "operator": "xianhuimeng",
                }
            ]
        }
        with patch("deeprca.agents.dimensions.change.query_recent_changes") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=mock_result)
            result = await analyze_change({
                "service_name": "order-service",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert isinstance(result, SubAgentResult)
        assert result.dimension == "change"
        assert result.confidence >= 0.7
        assert len(result.findings) == 1
        assert result.findings[0]["operator"] == "xianhuimeng"

    @pytest.mark.asyncio
    async def test_multiple_changes_higher_confidence(self):
        """2+ 条变更 → 置信度提升。"""
        mock_result = {
            "changes": [
                {"type": "deployment", "description": "发布 v1", "operator": "a"},
                {"type": "config", "description": "修改配置", "operator": "b"},
                {"type": "rollback", "description": "回滚", "operator": "c"},
            ]
        }
        with patch("deeprca.agents.dimensions.change.query_recent_changes") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=mock_result)
            result = await analyze_change({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence >= 0.9
        assert len(result.findings) == 3

    @pytest.mark.asyncio
    async def test_no_changes_low_confidence(self):
        """无变更记录 → 置信度 ≤ 0.1。"""
        with patch("deeprca.agents.dimensions.change.query_recent_changes") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value={"changes": []})
            result = await analyze_change({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence <= 0.1

    @pytest.mark.asyncio
    async def test_tool_error_degrades(self):
        """工具调用失败 → confidence=0, error 不为空。"""
        with patch("deeprca.agents.dimensions.change.query_recent_changes") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value={"error": "API timeout"})
            result = await analyze_change({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence == 0.0
        assert result.error is not None


# ─────────────────────────────────────────────
# 错误日志分析 Agent (§8)
# ─────────────────────────────────────────────

class TestErrorLogAnalyzer:
    """PRD-03 §8: OOM 错误 100 次/min → 置信度 ≥ 0.7。"""

    @pytest.mark.asyncio
    async def test_high_frequency_errors(self):
        """>20 条 ERROR 日志 → 置信度 ≥ 0.9。"""
        mock_logs = {
            "logs": [
                {"message": f"Error #{i}", "timestamp": "2026-07-10T13:55:00Z", "level": "ERROR"}
                for i in range(50)
            ]
        }
        with patch("deeprca.agents.dimensions.errorlog.query_error_logs") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=mock_logs)
            result = await analyze_errorlog({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert isinstance(result, SubAgentResult)
        assert result.dimension == "errorlog"
        assert result.confidence >= 0.9
        assert len(result.findings) == 50

    @pytest.mark.asyncio
    async def test_moderate_errors(self):
        """6~20 条 ERROR 日志 → 置信度 ≥ 0.7。"""
        mock_logs = {
            "logs": [
                {"message": f"Error #{i}", "level": "ERROR"}
                for i in range(10)
            ]
        }
        with patch("deeprca.agents.dimensions.errorlog.query_error_logs") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=mock_logs)
            result = await analyze_errorlog({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_no_errors(self):
        """无 ERROR 日志 → 置信度 ≤ 0.1。"""
        with patch("deeprca.agents.dimensions.errorlog.query_error_logs") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value={"logs": []})
            result = await analyze_errorlog({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence <= 0.1

    @pytest.mark.asyncio
    async def test_tool_error_degrades(self):
        """工具调用失败 → confidence=0, error 不为空。"""
        with patch("deeprca.agents.dimensions.errorlog.query_error_logs") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value={"error": "log API down"})
            result = await analyze_errorlog({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence == 0.0
        assert result.error is not None


# ─────────────────────────────────────────────
# 上游维度分析器
# ─────────────────────────────────────────────

class TestUpstreamAnalyzer:

    @pytest.mark.asyncio
    async def test_qps_drop_detected(self):
        """上游 QPS 突降 → 发现 upstream_qps_drop。"""
        mock_qps = {"data_points": [{"value": 100}, {"value": 100}, {"value": 30}]}
        mock_error = {"data_points": [{"value": 0.01}]}
        mock_topo = {"upstream": [{"service": "api-gateway"}]}

        with patch("deeprca.agents.dimensions.upstream.query_metrics") as mock_m, \
             patch("deeprca.agents.dimensions.upstream.query_topology") as mock_t:
            mock_m.ainvoke = AsyncMock(side_effect=[mock_qps, mock_error])
            mock_t.ainvoke = AsyncMock(return_value=mock_topo)
            result = await analyze_upstream({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
                "labels": {},
            })

        assert isinstance(result, SubAgentResult)
        assert result.dimension == "upstream"
        assert result.confidence > 0.0
        finding_types = {f["type"] for f in result.findings}
        assert "upstream_qps_drop" in finding_types

    @pytest.mark.asyncio
    async def test_no_anomaly(self):
        """无异常 → 置信度 ≤ 0.1。"""
        mock_qps = {"data_points": [{"value": 100}, {"value": 100}]}
        mock_error = {"data_points": [{"value": 0.01}]}
        mock_topo = {"upstream": []}

        with patch("deeprca.agents.dimensions.upstream.query_metrics") as mock_m, \
             patch("deeprca.agents.dimensions.upstream.query_topology") as mock_t:
            mock_m.ainvoke = AsyncMock(side_effect=[mock_qps, mock_error])
            mock_t.ainvoke = AsyncMock(return_value=mock_topo)
            result = await analyze_upstream({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
                "labels": {},
            })

        assert result.confidence <= 0.1


# ─────────────────────────────────────────────
# 下游维度分析器
# ─────────────────────────────────────────────

class TestDownstreamAnalyzer:

    @pytest.mark.asyncio
    async def test_slow_downstream_call(self):
        """下游调用超时 > 1000ms → 发现 slow_downstream_call。"""
        mock_trace = {
            "traces": [
                {
                    "trace_id": "trace-0001",
                    "spans": [
                        {"service": "svc", "operation": "handle_request", "duration_ms": 100, "status": "success"},
                        {"service": "db-query", "operation": "SELECT", "duration_ms": 1500, "status": "success"},
                    ],
                    "duration_ms": 1600,
                },
            ]
        }
        mock_topo = {"downstream": [{"service": "mysql-prod-01"}]}

        with patch("deeprca.agents.dimensions.downstream.query_trace") as mock_tr, \
             patch("deeprca.agents.dimensions.downstream.query_topology") as mock_t:
            mock_tr.ainvoke = AsyncMock(return_value=mock_trace)
            mock_t.ainvoke = AsyncMock(return_value=mock_topo)
            result = await analyze_downstream({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert isinstance(result, SubAgentResult)
        assert result.dimension == "downstream"
        finding_types = {f["type"] for f in result.findings}
        assert "slow_downstream_call" in finding_types

    @pytest.mark.asyncio
    async def test_downstream_error_status(self):
        """下游调用状态 ERROR → 发现 downstream_call_error。"""
        mock_trace = {
            "traces": [
                {
                    "trace_id": "trace-0002",
                    "spans": [
                        {"service": "svc", "operation": "handle_request", "duration_ms": 100, "status": "success"},
                        {"service": "rpc-call", "operation": "RPC", "duration_ms": 200, "status": "error"},
                    ],
                    "duration_ms": 300,
                },
            ]
        }
        mock_topo = {"downstream": []}

        with patch("deeprca.agents.dimensions.downstream.query_trace") as mock_tr, \
             patch("deeprca.agents.dimensions.downstream.query_topology") as mock_t:
            mock_tr.ainvoke = AsyncMock(return_value=mock_trace)
            mock_t.ainvoke = AsyncMock(return_value=mock_topo)
            result = await analyze_downstream({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        finding_types = {f["type"] for f in result.findings}
        assert "downstream_call_error" in finding_types


# ─────────────────────────────────────────────
# 集群维度分析器
# ─────────────────────────────────────────────

class TestClusterAnalyzer:

    @pytest.mark.asyncio
    async def test_cpu_high(self):
        """CPU > 80% → 发现 cpu_high。"""
        mock_cpu = {"data_points": [{"value": 85.0}, {"value": 90.0}]}
        mock_mem = {"data_points": [{"value": 60.0}]}
        mock_net = {"data_points": [{"value": 100.0}]}

        with patch("deeprca.agents.dimensions.cluster.query_metrics") as mock_m:
            mock_m.ainvoke = AsyncMock(side_effect=[mock_cpu, mock_mem, mock_net])
            result = await analyze_cluster({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
                "labels": {},
            })

        assert isinstance(result, SubAgentResult)
        assert result.dimension == "cluster"
        finding_types = {f["type"] for f in result.findings}
        assert "cpu_high" in finding_types

    @pytest.mark.asyncio
    async def test_memory_high(self):
        """内存 > 85% → 发现 memory_high。"""
        mock_cpu = {"data_points": [{"value": 50.0}]}
        mock_mem = {"data_points": [{"value": 90.0}]}
        mock_net = {"data_points": [{"value": 100.0}]}

        with patch("deeprca.agents.dimensions.cluster.query_metrics") as mock_m:
            mock_m.ainvoke = AsyncMock(side_effect=[mock_cpu, mock_mem, mock_net])
            result = await analyze_cluster({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
                "labels": {},
            })

        finding_types = {f["type"] for f in result.findings}
        assert "memory_high" in finding_types

    @pytest.mark.asyncio
    async def test_infra_keyword_detected(self):
        """告警描述包含 db 关键词 → 发现 infra_keyword。"""
        mock_cpu = {"data_points": []}
        mock_mem = {"data_points": []}
        mock_net = {"data_points": []}

        with patch("deeprca.agents.dimensions.cluster.query_metrics") as mock_m:
            mock_m.ainvoke = AsyncMock(side_effect=[mock_cpu, mock_mem, mock_net])
            result = await analyze_cluster({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
                "labels": {},
                "description": "MySQL database connection timeout",
            })

        finding_types = {f["type"] for f in result.findings}
        assert "infra_keyword" in finding_types


# ─────────────────────────────────────────────
# 关联告警分析器
# ─────────────────────────────────────────────

class TestProblemAnalyzer:

    @pytest.mark.asyncio
    async def test_cascade_alerts(self):
        """4+ 条关联告警 → 置信度 ≥ 0.9。"""
        mock_result = {
            "related_alerts": [
                {"alert_id": f"A00{i}", "service_name": f"svc-{i}", "description": f"alert {i}"}
                for i in range(5)
            ]
        }
        with patch("deeprca.agents.dimensions.problem.query_related_alerts") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=mock_result)
            result = await analyze_problem({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert isinstance(result, SubAgentResult)
        assert result.dimension == "problem"
        assert result.confidence >= 0.9
        assert len(result.findings) == 5

    @pytest.mark.asyncio
    async def test_no_related_alerts(self):
        """无关联告警 → 置信度 ≤ 0.1。"""
        with patch("deeprca.agents.dimensions.problem.query_related_alerts") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value={"related_alerts": []})
            result = await analyze_problem({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        assert result.confidence <= 0.1

    @pytest.mark.asyncio
    async def test_other_service_alerts(self):
        """发现其他服务告警 → 证据含级联提示。"""
        mock_result = {
            "related_alerts": [
                {"alert_id": "A001", "service_name": "other-svc", "description": "OOM"},
            ]
        }
        with patch("deeprca.agents.dimensions.problem.query_related_alerts") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=mock_result)
            result = await analyze_problem({
                "service_name": "svc",
                "timestamp": "2026-07-10T14:00:00Z",
            })

        evidence_text = " ".join(result.evidence)
        assert "其他服务" in evidence_text or "级联" in evidence_text
