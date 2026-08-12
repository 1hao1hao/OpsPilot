"""L3 根因定位 Agent 单元测试。PRD-04 §6-8。"""

import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from deeprca.agents.root_cause import RootCauseAgent, SUGGESTION_TEMPLATES


class TestRootCauseAgent:
    """L3 根因定位 Agent 测试。"""

    def setup_method(self):
        self.agent = RootCauseAgent()

    # ------------------------------------------------------------------ #
    #  规则命中路径
    # ------------------------------------------------------------------ #

    def test_rule_match_r002_db_slave_delay(self):
        """R002 命中时应直接返回规则结果，不调用 LLM。"""
        alert = {
            "trace_id": "trace-001",
            "service_name": "order-service",
            "alert_type": "timeout",
        }
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "db_expert",
                "dimension": "db",
                "confidence": 0.9,
                "findings": [
                    {"description": "slave_delay=15s 导致 timeout", "type": "slow_query"}
                ],
            }
        ]

        result = asyncio.run(
            self.agent.analyze(alert, evidence_summary, sub_agent_results)
        )

        assert result["rule_matched"] is True
        assert result["llm_used"] is False
        assert "candidates" in result
        assert len(result["candidates"]) >= 1
        best = result["best_candidate"]
        assert best is not None
        assert "主从延迟" in best["root_cause"]
        assert best["confidence"] >= 0.9
        assert best["source"] == "rule"

    def test_rule_match_r003_oom(self):
        """R003 OOM+重启应返回 0.95 置信度。"""
        alert = {
            "trace_id": "trace-002",
            "service_name": "payment-service",
            "alert_type": "error_rate",
        }
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "errorlog_analyzer",
                "dimension": "errorlog",
                "confidence": 0.9,
                "findings": [
                    {"description": "OutOfMemoryError, pod_restart OOMKilled", "type": "oom"}
                ],
            }
        ]

        result = asyncio.run(
            self.agent.analyze(alert, evidence_summary, sub_agent_results)
        )

        assert result["rule_matched"] is True
        best = result["best_candidate"]
        assert best["confidence"] >= 0.95
        assert "内存溢出" in best["root_cause"]

    def test_rule_boost_r001(self):
        """R001 变更 boost 应提升置信度。"""
        alert = {
            "trace_id": "trace-003",
            "service_name": "svc",
            "alert_type": "timeout",
        }
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "change_analyzer",
                "dimension": "change",
                "confidence": 0.8,
                "findings": [
                    {"description": "deployment 部署变更", "type": "deployment"}
                ],
            },
            {
                "agent_name": "db_expert",
                "dimension": "db",
                "confidence": 0.85,
                "findings": [
                    {"description": "slave_delay timeout 超时", "type": "slow_query"}
                ],
            },
        ]

        result = asyncio.run(
            self.agent.analyze(alert, evidence_summary, sub_agent_results)
        )

        # R002 应命中 + R001 boost
        assert result["rule_matched"] is True
        best = result["best_candidate"]
        # base 0.9 + boost 0.15 = 1.05, capped at 0.95
        assert best["confidence"] <= 0.95

    # ------------------------------------------------------------------ #
    #  LLM 降级路径
    # ------------------------------------------------------------------ #

    def test_llm_fallback_when_no_rule_match(self):
        """无规则匹配且 LLM 不可用时应降级返回。"""
        alert = {
            "trace_id": "trace-004",
            "service_name": "svc",
            "alert_type": "custom",
        }
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "cluster_analyzer",
                "dimension": "cluster",
                "confidence": 0.6,
                "findings": [
                    {"description": "CPU usage normal", "type": "info"}
                ],
            }
        ]

        result = asyncio.run(
            self.agent.analyze(alert, evidence_summary, sub_agent_results)
        )

        assert result["rule_matched"] is False
        assert result["llm_used"] is False
        assert len(result["candidates"]) == 3
        best = result["best_candidate"]
        assert best["source"] == "fallback"

    # ------------------------------------------------------------------ #
    #  证据链提取
    # ------------------------------------------------------------------ #

    def test_extract_evidence_chain(self):
        """证据链提取应从证据池和子 Agent 结果中收集。"""
        evidence = {
            "top_evidences": [
                {"dimension": "change", "finding": "deployment at 10:00", "confidence": 0.9},
                {"dimension": "db", "finding": "slave_delay=15s", "confidence": 0.85},
            ]
        }
        sub_agent_results = [
            {
                "agent_name": "db_expert",
                "dimension": "db",
                "confidence": 0.85,
                "findings": [
                    {"description": "slow query detected", "type": "slow_query"}
                ],
            }
        ]

        chain = self.agent._extract_evidence_chain(evidence, sub_agent_results)
        assert len(chain) >= 2
        # 按置信度排序
        assert chain[0]["confidence"] >= chain[1]["confidence"]

    def test_extract_evidence_chain_dedup(self):
        """证据链应去重。"""
        evidence = {
            "top_evidences": [
                {"dimension": "change", "finding": "same finding text", "confidence": 0.9},
            ]
        }
        sub_agent_results = [
            {
                "agent_name": "change_analyzer",
                "dimension": "change",
                "confidence": 0.8,
                "findings": [
                    {"description": "same finding text", "type": "deployment"}
                ],
            }
        ]

        chain = self.agent._extract_evidence_chain(evidence, sub_agent_results)
        assert len(chain) == 1

    # ------------------------------------------------------------------ #
    #  建议措施生成
    # ------------------------------------------------------------------ #

    def test_generate_suggestions_change(self):
        """变更根因应生成回滚建议。"""
        suggestions = self.agent._generate_suggestions(
            "配置变更导致异常", []
        )
        assert len(suggestions) > 0
        assert any("回滚" in s or "变更" in s for s in suggestions)

    def test_generate_suggestions_oom(self):
        """OOM 根因应生成内存分析建议。"""
        suggestions = self.agent._generate_suggestions(
            "服务内存溢出导致 Pod 被 Kill 并重启", []
        )
        assert len(suggestions) > 0
        assert any("内存" in s or "Heap" in s for s in suggestions)

    def test_generate_suggestions_dedup(self):
        """建议措施应去重。"""
        evidence_chain = [
            {"category": "change"},
            {"category": "change"},
        ]
        suggestions = self.agent._generate_suggestions(
            "变更导致故障", evidence_chain
        )
        # 不应有重复
        assert len(suggestions) == len(set(suggestions))

    def test_generate_suggestions_max_five(self):
        """建议措施最多 5 条。"""
        evidence_chain = [
            {"category": "change"},
            {"category": "db_slave_delay"},
            {"category": "redis_memory"},
            {"category": "kafka_lag"},
            {"category": "oom"},
            {"category": "resource_saturation"},
        ]
        suggestions = self.agent._generate_suggestions(
            "多因素叠加故障", evidence_chain
        )
        assert len(suggestions) <= 5

    # ------------------------------------------------------------------ #
    #  分类
    # ------------------------------------------------------------------ #

    def test_categorize_change(self):
        """变更根因应分类为 change。"""
        category = self.agent._categorize("配置变更导致异常", [])
        assert category == "change"

    def test_categorize_database(self):
        """数据库根因应分类为 database。"""
        category = self.agent._categorize("数据库主从延迟导致超时", [])
        assert category == "database"

    def test_categorize_unknown(self):
        """无法分类时应返回 unknown。"""
        category = self.agent._categorize("未知原因", [])
        assert category == "unknown"

    # ------------------------------------------------------------------ #
    #  异常检测
    # ------------------------------------------------------------------ #

    def test_anomaly_detection_with_baseline(self):
        """含 baseline_series 的指标应触发四分位检测。"""
        alert = {
            "metrics": {
                "tp99": {
                    "baseline_series": [10.0] * 15,
                    "current": 100.0,
                }
            }
        }
        anomalies = self.agent._run_anomaly_detection(alert)
        assert len(anomalies) > 0
        assert anomalies[0]["metric"] == "tp99"

    def test_anomaly_detection_empty_metrics(self):
        """空指标不应产生异常。"""
        anomalies = self.agent._run_anomaly_detection({})
        assert len(anomalies) == 0

    # ------------------------------------------------------------------ #
    #  指标筛选
    # ------------------------------------------------------------------ #

    def test_metric_filter_integration(self):
        """指标筛选应与 NoiseFilter 集成。"""
        metrics_data = {
            "error_rate": {"current": 0.15},
        }
        anomalies = self.agent._run_metric_filter(metrics_data)
        assert len(anomalies) > 0
        # NoiseFilter 不应过滤掉高偏离异常
        filtered = self.agent.noise_filter.filter_noise(anomalies)
        assert len(filtered) > 0

    # ------------------------------------------------------------------ #
    #  完整流程 - LLM 模拟
    # ------------------------------------------------------------------ #

    def test_full_flow_with_mocked_llm(self):
        """无规则匹配时，模拟 LLM 返回结果。"""
        alert = {
            "trace_id": "trace-mock",
            "service_name": "test-service",
            "alert_type": "custom",
            "metrics": {},
        }
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "cluster_analyzer",
                "dimension": "cluster",
                "confidence": 0.6,
                "findings": [
                    {"description": "CPU usage normal", "type": "info"}
                ],
            }
        ]

        # Mock LLM 调用
        mock_response = '''{"candidates": [
            {"rank": 1, "root_cause": "CPU 资源不足", "confidence": 0.7, "evidence_chain": ["cluster: CPU usage"]},
            {"rank": 2, "root_cause": "内存泄漏", "confidence": 0.5, "evidence_chain": ["memory analysis"]},
            {"rank": 3, "root_cause": "未知因素", "confidence": 0.3, "evidence_chain": []}
        ]}'''

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.run(
                self.agent.analyze(alert, evidence_summary, sub_agent_results)
            )

        assert result["rule_matched"] is False
        assert result["llm_used"] is True
        assert len(result["candidates"]) == 3
        best = result["best_candidate"]
        assert best["root_cause"] == "CPU 资源不足"
        assert best["source"] == "llm"
        assert "suggestions" in result
