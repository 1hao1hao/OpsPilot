"""LangGraph 主编排图单元测试。"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from deeprca.graph.state import DeepRCAState, TaskPlan
from deeprca.graph.main_graph import build_coordinator_graph


class TestGraphBuilding:
    """图构建测试。"""

    def test_build_graph_returns_compiled(self):
        """build_coordinator_graph 应返回编译后的图。"""
        graph = build_coordinator_graph()
        assert graph is not None
        assert hasattr(graph, "ainvoke") or hasattr(graph, "invoke")


class TestIntakeNode:
    """intake 节点测试。"""

    def test_intake_parses_alert(self):
        """intake 应正确解析告警字段。"""
        from deeprca.agents.coordinator import intake_node

        state = {
            "alert": {
                "alert_id": "alt-001",
                "service_name": "order-service",
                "alert_type": "timeout",
                "severity": "P1",
                "timestamp": "2026-07-13T10:00:00+00:00",
                "description": "接口超时",
                "labels": {"cluster": "prod", "env": "production", "app": "order"},
            }
        }
        result = intake_node(state)

        assert result["alert"]["service_name"] == "order-service"
        assert result["alert"]["alert_type"] == "timeout"
        assert result["alert"]["cluster"] == "prod"
        assert result["alert"]["env"] == "production"
        assert result["trace_id"].startswith("trace-")
        assert result["status"] == "running"

    def test_intake_with_missing_fields(self):
        """intake 应处理缺失字段。"""
        from deeprca.agents.coordinator import intake_node

        state = {"alert": {}}
        result = intake_node(state)

        assert result["alert"]["service_name"] == "unknown"
        assert result["alert"]["alert_type"] == "custom"
        assert result["alert"]["severity"] == "P2"

    def test_intake_generates_time_window(self):
        """intake 应生成时间窗口。"""
        from deeprca.agents.coordinator import intake_node

        state = {
            "alert": {
                "alert_id": "alt-001",
                "service_name": "test-service",
                "timestamp": "2026-07-13T10:00:00+00:00",
            }
        }
        result = intake_node(state)

        assert "time_window_start" in result["alert"]
        assert "time_window_end" in result["alert"]

    def test_intake_time_window_by_type(self):
        """intake 应按告警类型推导不同时间窗口。"""
        from deeprca.agents.coordinator import intake_node

        # timeout 类型: before=30m
        state_timeout = {
            "alert": {
                "alert_id": "alt-001",
                "service_name": "test",
                "alert_type": "timeout",
                "timestamp": "2026-07-13T10:00:00+00:00",
            }
        }
        result_t = intake_node(state_timeout)
        # window_start 应在 09:30 左右
        assert "09:30" in result_t["alert"]["time_window_start"]

        # resource 类型: before=60m
        state_resource = {
            "alert": {
                "alert_id": "alt-002",
                "service_name": "test",
                "alert_type": "resource",
                "timestamp": "2026-07-13T10:00:00+00:00",
            }
        }
        result_r = intake_node(state_resource)
        # window_start 应在 09:00 左右
        assert "09:00" in result_r["alert"]["time_window_start"]

    def test_intake_extracts_related_services(self):
        """intake 应提取关联服务列表。"""
        from deeprca.agents.coordinator import intake_node

        state = {
            "alert": {
                "alert_id": "alt-001",
                "service_name": "order-service",
                "alert_type": "timeout",
                "timestamp": "2026-07-13T10:00:00+00:00",
                "description": "order-service 调用 payment-service 超时",
                "labels": {"app": "order"},
            }
        }
        result = intake_node(state)

        assert "related_services" in result
        assert "order-service" in result["related_services"]
        assert "payment-service" in result["related_services"]


class TestPlannerNode:
    """planner 节点测试。"""

    def test_planner_generates_six_dimensions(self):
        """planner 应对 timeout 类型生成 6 个维度的分析任务。"""
        from deeprca.agents.coordinator import planner_node

        state = {
            "alert": {
                "service_name": "order-service",
                "alert_type": "timeout",
                "time_window_start": "2026-07-13T09:30:00+00:00",
                "time_window_end": "2026-07-13T10:00:00+00:00",
            }
        }
        result = planner_node(state)

        assert len(result["task_plan"]) == 6
        dimensions = [t["dimension"] for t in result["task_plan"]]
        assert "change" in dimensions
        assert "upstream" in dimensions
        assert "downstream" in dimensions
        assert "cluster" in dimensions
        assert "errorlog" in dimensions
        assert "problem" in dimensions

    def test_planner_resource_type_four_dimensions(self):
        """planner 应对 resource 类型生成 4 个维度。"""
        from deeprca.agents.coordinator import planner_node

        state = {
            "alert": {
                "service_name": "test-service",
                "alert_type": "resource",
                "time_window_start": "2026-07-13T09:30:00+00:00",
                "time_window_end": "2026-07-13T10:00:00+00:00",
            }
        }
        result = planner_node(state)

        assert len(result["task_plan"]) == 4
        dimensions = [t["dimension"] for t in result["task_plan"]]
        assert "upstream" not in dimensions
        assert "downstream" not in dimensions

    def test_planner_sorts_by_priority(self):
        """planner 应按优先级排序。"""
        from deeprca.agents.coordinator import planner_node

        state = {
            "alert": {"service_name": "test", "alert_type": "custom", "time_window_start": "", "time_window_end": ""}
        }
        result = planner_node(state)

        priorities = [t["priority"] for t in result["task_plan"]]
        assert priorities == sorted(priorities)

    def test_planner_includes_task_id_and_tools(self):
        """planner 生成的任务应包含 task_id 和 tools 字段。"""
        from deeprca.agents.coordinator import planner_node

        state = {
            "alert": {
                "alert_id": "alt-001",
                "service_name": "test",
                "alert_type": "timeout",
                "time_window_start": "",
                "time_window_end": "",
            }
        }
        result = planner_node(state)

        for task in result["task_plan"]:
            assert "task_id" in task
            assert "tools" in task
            assert "name" in task
            assert "description" in task


class TestCollectorNode:
    """collector 节点测试。"""

    def test_collector_aggregates_evidence(self):
        """collector 应聚合证据。"""
        from deeprca.agents.coordinator import collector_node

        state = {
            "sub_agent_results": [
                {
                    "agent_name": "change_analyzer",
                    "dimension": "change",
                    "confidence": 0.8,
                    "findings": [{"desc": "发现最近部署"}],
                    "timestamp": "2026-07-13T10:01:00+00:00",
                },
            ]
        }
        result = collector_node(state)
        assert "collected_evidence" in result

    def test_collector_handles_empty(self):
        """collector 应处理空结果。"""
        from deeprca.agents.coordinator import collector_node

        state = {"sub_agent_results": []}
        result = collector_node(state)
        assert "collected_evidence" in result


class TestReporterNode:
    """reporter 节点测试。"""

    def test_reporter_generates_report(self):
        """reporter 应生成分析报告。"""
        from deeprca.agents.coordinator import reporter_node

        state = {
            "alert": {"alert_id": "alt-001", "service_name": "order-service", "severity": "P1"},
            "root_cause": {
                "candidates": [
                    {"rank": 1, "root_cause": "DB 慢查询", "confidence": 0.85, "evidence_chain": [], "matched_rule": "R001", "source": "rule"}
                ],
                "best_candidate": {"rank": 1, "root_cause": "DB 慢查询", "confidence": 0.85, "evidence_chain": [], "matched_rule": "R001", "source": "rule"},
                "anomalies_detected": [],
                "rule_matched": True,
                "llm_used": False,
                "trace_id": "trace-test",
                "timestamp": "2026-07-13T10:02:00+00:00",
            },
            "collected_evidence": {"top_evidences": [{"finding": "慢查询激增"}]},
            "sub_agent_results": [{"agent_name": "db_expert", "dimension": "db", "confidence": 0.9, "findings": []}],
            "trace_id": "trace-test",
            "start_time": "2026-07-13T10:00:00+00:00",
        }
        result = reporter_node(state)

        assert result["status"] == "completed"
        report = json.loads(result["report"])
        assert report["trace_id"] == "trace-test"
        assert report["root_cause"] == "DB 慢查询"
        assert report["confidence"] == 0.85

    def test_reporter_includes_suggestions(self):
        """reporter 应生成建议措施。"""
        from deeprca.agents.coordinator import reporter_node

        state = {
            "alert": {"alert_id": "alt-001", "service_name": "order-service", "severity": "P1"},
            "root_cause": {
                "candidates": [],
                "best_candidate": {"rank": 1, "root_cause": "DB 主从延迟", "confidence": 0.8, "source": "rule"},
                "anomalies_detected": [],
                "rule_matched": False,
                "llm_used": False,
            },
            "collected_evidence": {"top_evidences": []},
            "sub_agent_results": [],
            "trace_id": "trace-test",
            "start_time": "2026-07-13T10:00:00+00:00",
        }
        result = reporter_node(state)
        report = json.loads(result["report"])

        assert "suggestions" in report
        assert len(report["suggestions"]) > 0
        assert "satisfaction_url" in report
        assert "trace-test" in report["satisfaction_url"]

    def test_reporter_degraded_mode(self):
        """reporter 在降级模式应生成降级建议。"""
        from deeprca.agents.coordinator import reporter_node

        state = {
            "alert": {"alert_id": "alt-001", "service_name": "test", "severity": "P1"},
            "root_cause": {"candidates": [], "best_candidate": None, "anomalies_detected": [], "rule_matched": False, "llm_used": False},
            "collected_evidence": {"top_evidences": []},
            "sub_agent_results": [],
            "trace_id": "trace-test",
            "start_time": "2026-07-13T10:00:00+00:00",
            "degraded_mode": True,
        }
        result = reporter_node(state)
        report = json.loads(result["report"])

        assert len(report["suggestions"]) > 0
        assert any("人工" in s for s in report["suggestions"])


class TestCheckTimeout:
    """超时检查条件边测试。"""

    def test_normal_when_no_start_time(self):
        from deeprca.agents.coordinator import check_timeout
        assert check_timeout({}) == "normal"

    def test_normal_within_timeout(self):
        from deeprca.agents.coordinator import check_timeout
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        assert check_timeout({"start_time": recent}) == "normal"

    def test_timeout_when_exceeded(self):
        from deeprca.agents.coordinator import check_timeout
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        assert check_timeout({"start_time": old}) == "timeout"
