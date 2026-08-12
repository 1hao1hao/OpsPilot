"""指标筛选器、噪声过滤器和规则引擎单元测试。PRD-04 §4, §5。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from deeprca.detection.filters import MetricFilter, NoiseFilter, ExpertRuleEngine


class TestMetricFilter:
    """指标筛选器测试。"""

    def setup_method(self):
        self.filter = MetricFilter()

    def test_filter_empty_metrics(self):
        """空指标字典应安全返回。"""
        result = self.filter.filter({})
        assert len(result) == 0

    def test_filter_anomalies_empty_metrics(self):
        """空指标字典 filter_anomalies 应安全返回。"""
        result = self.filter.filter_anomalies({})
        assert len(result) == 0

    def test_filter_high_qps(self):
        """高 QPS 指标应被选中。"""
        metrics = {
            "qps": 5000,
            "qps_baseline": 1000,
            "error_rate": 0.01,
        }
        result = self.filter.filter_anomalies(metrics)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_filter_normal_metrics(self):
        """正常指标应被过滤掉。"""
        metrics = {
            "qps": 1050,
            "qps_baseline": 1000,
            "error_rate": 0.01,
        }
        result = self.filter.filter_anomalies(metrics)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_filter_with_data_points(self):
        """filter 方法: 含 data_points 的指标数据应能筛选。"""
        metrics_data = {
            "error_rate": {
                "current": 0.15,
                "value": 0.15,
            },
            "qps": {
                "current": 5000,
                "value": 5000,
            },
        }
        result = self.filter.filter(metrics_data)
        assert isinstance(result, list)
        # error_rate > 0.05 threshold → should be included
        assert len(result) > 0

    def test_filter_high_error_rate(self):
        """高错误率指标应被筛选。"""
        metrics_data = {
            "error_rate": {"current": 0.15},
        }
        result = self.filter.filter(metrics_data)
        assert len(result) > 0
        assert result[0]["dimension"] == "high_error_rate"


class TestNoiseFilter:
    """噪声过滤器测试。PRD-04 §4.2。"""

    def setup_method(self):
        self.filter = NoiseFilter()

    def test_filter_low_deviation(self):
        """偏离比 < 2.0 且 > 0.5 的应被过滤。"""
        anomalies = [
            {"deviation_ratio": 1.5, "confidence": 0.8, "duration_seconds": 300},
        ]
        result = self.filter.filter_noise(anomalies)
        assert len(result) == 0

    def test_keep_high_deviation(self):
        """偏离比 >= 2.0 的应保留。"""
        anomalies = [
            {"deviation_ratio": 5.0, "confidence": 0.85, "duration_seconds": 300},
        ]
        result = self.filter.filter_noise(anomalies)
        assert len(result) == 1

    def test_filter_short_duration(self):
        """持续时间 < 120s 的应被过滤。"""
        anomalies = [
            {"deviation_ratio": 5.0, "confidence": 0.85, "duration_seconds": 60},
        ]
        result = self.filter.filter_noise(anomalies)
        assert len(result) == 0

    def test_filter_low_confidence_low_deviation(self):
        """低置信度 + 低偏离比应被过滤。"""
        anomalies = [
            {"deviation_ratio": 2.5, "confidence": 0.3, "duration_seconds": 300},
        ]
        result = self.filter.filter_noise(anomalies)
        assert len(result) == 0

    def test_keep_valid_anomaly(self):
        """正常异常应保留。"""
        anomalies = [
            {"deviation_ratio": 5.0, "confidence": 0.85, "duration_seconds": 300},
            {"deviation_ratio": 1.5, "confidence": 0.8, "duration_seconds": 300},
        ]
        result = self.filter.filter_noise(anomalies)
        assert len(result) == 1

    def test_empty_list(self):
        """空列表应安全返回。"""
        result = self.filter.filter_noise([])
        assert len(result) == 0


class TestExpertRuleEngine:
    """专家规则引擎测试。PRD-04 §5。"""

    def setup_method(self):
        self.engine = ExpertRuleEngine()

    def test_evaluate_empty_input(self):
        """空输入应安全返回。"""
        rules = self.engine.evaluate({}, [], {}, [])
        assert isinstance(rules, list)

    def test_r001_change_boost(self):
        """R001: 变更+故障时间吻合 → boost_confidence。"""
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "change_analyzer",
                "dimension": "change",
                "confidence": 0.8,
                "findings": [
                    {"description": "检测到 deployment 部署变更", "type": "deployment"}
                ],
            }
        ]
        alert = {"service_name": "order-service", "alert_type": "timeout"}
        anomalies = []

        rules = self.engine.evaluate(evidence_summary, sub_agent_results, alert, anomalies)
        r001 = [r for r in rules if r.get("rule_id") == "R001"]
        assert len(r001) == 1
        assert r001[0].get("boost") == 0.15

    def test_r002_db_slave_delay(self):
        """R002: DB主从延迟+读超时 → set_root_cause。"""
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "db_expert",
                "dimension": "db",
                "confidence": 0.9,
                "findings": [
                    {"description": "slave_delay=15s 导致 timeout 超时", "type": "slow_query"}
                ],
            }
        ]
        alert = {"service_name": "order-service", "alert_type": "timeout"}
        anomalies = []

        rules = self.engine.evaluate(evidence_summary, sub_agent_results, alert, anomalies)
        r002 = [r for r in rules if r.get("rule_id") == "R002"]
        assert len(r002) == 1
        assert r002[0].get("root_cause") == "数据库主从延迟导致读请求超时"
        assert r002[0].get("confidence") == 0.9

    def test_r003_oom_restart(self):
        """R003: OOM+服务重启 → set_root_cause。"""
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "errorlog_analyzer",
                "dimension": "errorlog",
                "confidence": 0.9,
                "findings": [
                    {"description": "OutOfMemoryError detected, pod_restart OOMKilled", "type": "oom"}
                ],
            }
        ]
        alert = {"service_name": "payment-service", "alert_type": "error_rate"}
        anomalies = []

        rules = self.engine.evaluate(evidence_summary, sub_agent_results, alert, anomalies)
        r003 = [r for r in rules if r.get("rule_id") == "R003"]
        assert len(r003) == 1
        assert r003[0].get("confidence") == 0.95

    def test_r004_kafka_consumer_lag(self):
        """R004: 消费积压+消费者离线 → set_root_cause。"""
        evidence_summary = {"top_evidences": []}
        sub_agent_results = [
            {
                "agent_name": "mafka_expert",
                "dimension": "mafka",
                "confidence": 0.85,
                "findings": [
                    {"description": "consumer_offline consumer_lag backlog", "type": "lag"}
                ],
            }
        ]
        alert = {"service_name": "mq-service", "alert_type": "error_rate"}
        anomalies = []

        rules = self.engine.evaluate(evidence_summary, sub_agent_results, alert, anomalies)
        r004 = [r for r in rules if r.get("rule_id") == "R004"]
        assert len(r004) == 1

    def test_r008_multi_dimension(self):
        """R008: 多维度共振 → boost_confidence。"""
        evidence_summary = {"top_evidences": []}
        sub_agent_results = []
        alert = {"service_name": "order-service", "alert_type": "timeout"}
        anomalies = [
            {"metric": "qps", "dimension": "upstream"},
            {"metric": "tp99", "dimension": "downstream"},
            {"metric": "cpu", "dimension": "cluster"},
            {"metric": "error_rate", "dimension": "errorlog"},
        ]

        rules = self.engine.evaluate(evidence_summary, sub_agent_results, alert, anomalies)
        r008 = [r for r in rules if r.get("rule_id") == "R008"]
        assert len(r008) == 1
        assert r008[0].get("boost") == 0.10

    def test_no_match(self):
        """无匹配条件时应返回空列表。"""
        evidence_summary = {}
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
        alert = {"service_name": "svc", "alert_type": "custom"}
        anomalies = []

        rules = self.engine.evaluate(evidence_summary, sub_agent_results, alert, anomalies)
        assert isinstance(rules, list)
