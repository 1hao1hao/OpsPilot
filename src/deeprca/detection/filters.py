"""指标筛选器、噪声过滤器和专家规则引擎。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.3-3.4.4</td></tr>
<tr><td>0.2.0</td><td>PRD-04 对齐: NoiseFilter 新增、ExpertRuleEngine 规则修正为 R001-R008</td><td>PRD-04 §4, §5</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import logging

from deeprca.detection.quantile import QuantileAnomalyDetector

logger = logging.getLogger(__name__)


class MetricFilter:
    """多维指标筛选器。PRD-04 §4.1。

    筛选高QPS、高失败率、TP99突变等关键异常指标。
    """

    FILTER_DIMENSIONS = {
        "high_qps": {
            "description": "高 QPS 指标",
            "threshold": 1000,
            "metric": "qps",
            "weight": 1.0,
        },
        "high_error_rate": {
            "description": "高失败率指标",
            "threshold": 0.05,
            "metric": "error_rate",
            "weight": 1.5,
        },
        "tp99_spike": {
            "description": "TP99 突变",
            "metric": "tp99",
            "anomaly_detector": True,
            "weight": 1.5,
        },
        "tp95_spike": {
            "description": "TP95 突变",
            "metric": "tp95",
            "anomaly_detector": True,
            "weight": 1.0,
        },
        "resource_saturation": {
            "description": "资源饱和度",
            "metrics": ["cpu_usage", "memory_usage", "disk_usage"],
            "threshold": 0.85,
            "weight": 1.2,
        },
    }

    DEFAULT_THRESHOLDS = {
        "error_rate": 0.05,
        "tp99_deviation": 3.0,
        "qps_deviation": 3.0,
    }

    def filter(self, metrics_data: dict) -> list[dict]:
        """PRD-04 §4.1: 筛选关键异常指标。

        Args:
            metrics_data: 所有指标数据，格式如:
                {"qps": {"data_points": [{"value": 100}, ...]}, ...}

        Returns:
            筛选后的关键异常指标列表，按置信度排序
        """
        results: list[dict] = []
        detector = QuantileAnomalyDetector()

        for dim_name, config in self.FILTER_DIMENSIONS.items():
            metric_names = config.get("metrics", [config.get("metric")])
            if not metric_names:
                continue

            for metric_name in metric_names:
                if not metric_name:
                    continue
                data = metrics_data.get(metric_name)
                if not data or not isinstance(data, dict):
                    continue

                if config.get("anomaly_detector"):
                    # 使用异常检测算法
                    points = data.get("data_points", [])
                    if not points:
                        continue
                    values = [p.get("value", 0) for p in points if isinstance(p, dict)]
                    current = values[-1] if values else 0
                    baseline = values[:-1] if len(values) > 1 else []
                    anomaly = detector.detect(baseline, current)
                    if anomaly.is_anomaly:
                        results.append({
                            "dimension": dim_name,
                            "metric": metric_name,
                            "current_value": current,
                            "baseline_value": anomaly.baseline_value,
                            "deviation_ratio": anomaly.deviation_ratio,
                            "severity": anomaly.severity,
                            "confidence": anomaly.confidence * config["weight"],
                            "anomaly_type": anomaly.anomaly_type,
                        })
                else:
                    # 阈值判断
                    threshold = config.get("threshold")
                    current = data.get("current") or data.get("value", 0)
                    if threshold and current > threshold:
                        results.append({
                            "dimension": dim_name,
                            "metric": metric_name,
                            "current_value": current,
                            "threshold": threshold,
                            "deviation_ratio": current / threshold if threshold else 0,
                            "severity": "high" if current > threshold * 2 else "medium",
                            "confidence": 0.8 * config["weight"],
                        })

        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results

    def filter_anomalies(self, metrics: dict, thresholds: dict | None = None) -> list[dict]:
        """兼容旧接口：简单阈值筛选。"""
        if thresholds is None:
            thresholds = self.DEFAULT_THRESHOLDS

        results: list[dict] = []

        error_rate = metrics.get("error_rate")
        if error_rate is not None:
            threshold = thresholds.get("error_rate", self.DEFAULT_THRESHOLDS["error_rate"])
            if error_rate > threshold:
                results.append({
                    "metric": "error_rate",
                    "value": float(error_rate),
                    "threshold": threshold,
                    "type": "error_rate",
                })

        tp99 = metrics.get("tp99")
        tp99_baseline = metrics.get("tp99_baseline")
        if tp99 is not None and tp99_baseline is not None and tp99_baseline != 0:
            tp99_dev = tp99 / tp99_baseline
            threshold = thresholds.get("tp99_deviation", self.DEFAULT_THRESHOLDS["tp99_deviation"])
            if tp99_dev > threshold:
                results.append({
                    "metric": "tp99",
                    "value": round(float(tp99_dev), 4),
                    "threshold": threshold,
                    "type": "tp99_deviation",
                })

        qps = metrics.get("qps")
        qps_baseline = metrics.get("qps_baseline")
        if qps is not None and qps_baseline is not None and qps_baseline != 0:
            qps_dev = qps / qps_baseline
            threshold = thresholds.get("qps_deviation", self.DEFAULT_THRESHOLDS["qps_deviation"])
            if qps_dev > threshold:
                results.append({
                    "metric": "qps",
                    "value": round(float(qps_dev), 4),
                    "threshold": threshold,
                    "type": "qps_deviation",
                })

        return results


class NoiseFilter:
    """低影响抖动过滤器。PRD-04 §4.2。

    过滤偏离比小、持续时间短的瞬态抖动，保留真正的异常。
    """

    def filter_noise(self, anomalies: list[dict]) -> list[dict]:
        """过滤低影响抖动，保留真正的异常。

        规则:
        1. 偏离比 < 2.0 且 > 0.5 → 低影响抖动，过滤
        2. 持续时间 < 120s → 瞬态抖动，过滤
        3. 低置信度(<0.5) + 低偏离比(<3.0) → 过滤
        """
        filtered: list[dict] = []
        for anomaly in anomalies:
            deviation = anomaly.get("deviation_ratio", 1.0)

            # 规则 1: 偏离比 < 2 倍的视为低影响抖动
            if 0.5 < deviation < 2.0:
                continue

            # 规则 2: 持续时间 < 2 分钟的瞬态抖动过滤
            if anomaly.get("duration_seconds", 999) < 120:
                continue

            # 规则 3: 低置信度 + 低偏离比
            if anomaly.get("confidence", 0) < 0.5 and deviation < 3.0:
                continue

            filtered.append(anomaly)

        return filtered


class ExpertRuleEngine:
    """专家经验规则引擎。PRD-04 §5。

    8 条确定性规则 (R001-R008)。
    规则匹配基于证据文本关键词和条件组合。
    """

    RULES = [
        {
            "rule_id": "R001",
            "name": "变更+故障时间吻合",
            "condition": {
                "change_within_minutes": 30,
                "evidence_contains_any": ["deployment", "config", "变更", "deploy", "发布"],
            },
            "action": "boost_confidence",
            "boost": 0.15,
            "description": "30分钟内有变更且告警类型为超时或错误率，提升根因为变更的置信度",
        },
        {
            "rule_id": "R002",
            "name": "DB主从延迟+读超时",
            "condition": {
                "evidence_contains_any": ["slave_delay", "主从延迟"],
                "evidence_contains_any_2": ["timeout", "超时", "Lock wait"],
            },
            "action": "set_root_cause",
            "root_cause": "数据库主从延迟导致读请求超时",
            "confidence": 0.9,
        },
        {
            "rule_id": "R003",
            "name": "OOM+服务重启",
            "condition": {
                "evidence_contains_any": ["OutOfMemoryError", "OOM", "oom", "内存溢出"],
                "evidence_contains_any_2": ["pod_restart", "OOMKilled", "重启", "restart"],
            },
            "action": "set_root_cause",
            "root_cause": "服务内存溢出导致 Pod 被 Kill 并重启",
            "confidence": 0.95,
        },
        {
            "rule_id": "R004",
            "name": "消费积压+消费者离线",
            "condition": {
                "evidence_contains_any": ["consumer_offline", "消费者离线", "consumer_lag"],
                "evidence_contains_any_2": ["consumer_lag", "rebalance", "积压", "backlog"],
            },
            "action": "set_root_cause",
            "root_cause": "Kafka 消费者离线导致消息积压",
            "confidence": 0.9,
        },
        {
            "rule_id": "R005",
            "name": "流量突增+资源饱和",
            "condition": {
                "evidence_contains_any": ["qps_spike", "流量突增", "traffic_spike"],
                "evidence_contains_any_2": ["cpu_usage", "memory_usage", "resource", "资源饱和"],
            },
            "action": "set_root_cause",
            "root_cause": "上游流量突增导致服务资源饱和",
            "confidence": 0.85,
        },
        {
            "rule_id": "R006",
            "name": "熔断触发+下游异常",
            "condition": {
                "evidence_contains_any": ["circuit_breaker", "熔断"],
                "evidence_contains_any_2": ["downstream", "下游", "dependency"],
            },
            "action": "boost_confidence",
            "boost": 0.10,
            "description": "熔断器触发且下游存在异常，提升下游异常的根因置信度",
        },
        {
            "rule_id": "R007",
            "name": "配置变更+连接池异常",
            "condition": {
                "evidence_contains_any": ["config", "配置变更"],
                "evidence_contains_any_2": ["connection_pool", "active_connections", "连接池"],
            },
            "action": "set_root_cause",
            "root_cause": "配置变更导致连接池参数异常",
            "confidence": 0.85,
        },
        {
            "rule_id": "R008",
            "name": "多维度共振",
            "condition": {
                "anomaly_dimensions_count": 3,
            },
            "action": "boost_confidence",
            "boost": 0.10,
            "description": "3个以上维度同时异常，提升整体根因置信度",
        },
    ]

    def _build_context_text(
        self,
        evidence_pool_summary: dict,
        sub_agent_results: list[dict],
        alert: dict,
        anomalies: list[dict],
    ) -> str:
        """将所有上下文信息拼接为文本，用于关键词匹配。"""
        parts: list[str] = []

        # 证据摘要
        top_evidences = evidence_pool_summary.get("top_evidences", [])
        for ev in top_evidences:
            if isinstance(ev, dict):
                parts.append(ev.get("finding", ""))
                parts.append(ev.get("dimension", ""))
            else:
                parts.append(str(ev))

        # 子 Agent findings
        for result in sub_agent_results:
            for finding in result.get("findings", []):
                if isinstance(finding, dict):
                    parts.append(finding.get("description", ""))
                    parts.append(finding.get("desc", ""))
                    parts.append(finding.get("type", ""))
                    parts.append(finding.get("metric", ""))
                else:
                    parts.append(str(finding))
            parts.append(result.get("dimension", ""))

        # 告警信息
        parts.append(alert.get("alert_name", ""))
        parts.append(alert.get("alert_type", ""))
        parts.append(alert.get("description", ""))
        parts.append(alert.get("service_name", ""))

        # 异常列表
        for anomaly in anomalies:
            parts.append(anomaly.get("metric", ""))
            parts.append(anomaly.get("type", ""))

        return " ".join(p for p in parts if p).lower()

    def _check_condition(self, condition: dict, context_text: str, anomalies: list[dict]) -> bool:
        """检查单个规则条件是否满足。"""
        # 检查 evidence_contains（全部包含）
        if "evidence_contains" in condition:
            for keyword in condition["evidence_contains"]:
                if keyword.lower() not in context_text:
                    return False

        # 检查 evidence_contains_any（任一包含）
        if "evidence_contains_any" in condition:
            if not any(kw.lower() in context_text for kw in condition["evidence_contains_any"]):
                return False

        # 检查 evidence_contains_any_2（第二个任一组）
        if "evidence_contains_any_2" in condition:
            if not any(kw.lower() in context_text for kw in condition["evidence_contains_any_2"]):
                return False

        # 检查异常维度数量
        if "anomaly_dimensions_count" in condition:
            anomaly_dims = set()
            for a in anomalies:
                dim = a.get("dimension") or a.get("metric", "")
                if dim:
                    anomaly_dims.add(dim)
            if len(anomaly_dims) < condition["anomaly_dimensions_count"]:
                return False

        return True

    def evaluate(
        self,
        evidence_pool_summary: dict,
        sub_agent_results: list[dict],
        alert: dict,
        anomalies: list[dict],
    ) -> list[dict]:
        """评估所有规则。

        Returns:
            匹配的规则列表，每条含:
            - set_root_cause: {"rule_id", "root_cause", "confidence", "name"}
            - boost_confidence: {"rule_id", "boost", "description", "name"}
        """
        context_text = self._build_context_text(
            evidence_pool_summary, sub_agent_results, alert, anomalies
        )

        matched_rules: list[dict] = []

        for rule in self.RULES:
            condition = rule.get("condition", {})
            if not self._check_condition(condition, context_text, anomalies):
                continue

            if rule["action"] == "set_root_cause":
                matched_rules.append({
                    "matched": True,
                    "rule_id": rule["rule_id"],
                    "name": rule["name"],
                    "root_cause": rule["root_cause"],
                    "confidence": rule["confidence"],
                })
            elif rule["action"] == "boost_confidence":
                matched_rules.append({
                    "matched": True,
                    "rule_id": rule["rule_id"],
                    "name": rule["name"],
                    "boost": rule["boost"],
                    "description": rule.get("description", ""),
                })

        return matched_rules
