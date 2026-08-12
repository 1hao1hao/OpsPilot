"""量化异常检测算法单元测试。PRD-04 §3.1。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from deeprca.detection.quantile import QuantileAnomalyDetector, AnomalyResult


class TestQuantileAnomalyDetector:
    """四分位 IQR 异常检测测试。"""

    def setup_method(self):
        self.detector = QuantileAnomalyDetector()

    def test_normal_value_no_anomaly(self):
        """正常值不应触发异常。"""
        baseline = [10.0, 11.0, 10.5, 11.5, 10.2, 10.8, 11.2, 10.9, 10.3, 11.0]
        result = self.detector.detect(baseline, 10.7)
        assert not result.is_anomaly
        assert result.anomaly_type == "normal"

    def test_high_outlier_anomaly(self):
        """远高于上界的值应触发 spike 异常。"""
        baseline = [10.0, 11.0, 10.5, 11.5, 10.2, 10.8, 11.2, 10.9, 10.3, 11.0]
        result = self.detector.detect(baseline, 100.0)
        assert result.is_anomaly
        assert result.anomaly_type == "spike"
        assert result.confidence > 0.5

    def test_low_outlier_anomaly(self):
        """远低于下界的值应触发 drop 异常。"""
        baseline = [10.0, 11.0, 10.5, 11.5, 10.2, 10.8, 11.2, 10.9, 10.3, 11.0]
        result = self.detector.detect(baseline, -50.0)
        assert result.is_anomaly
        assert result.anomaly_type == "drop"

    def test_empty_baseline(self):
        """空基线应返回 insufficient_data。"""
        result = self.detector.detect([], 10.0)
        assert not result.is_anomaly
        assert result.anomaly_type == "insufficient_data"

    def test_insufficient_data(self):
        """数据点不足 min_points 应返回 insufficient_data。"""
        result = self.detector.detect([10.0, 11.0], 10.0)
        assert not result.is_anomaly
        assert result.anomaly_type == "insufficient_data"

    def test_severity_critical(self):
        """极大偏离应标记为 critical 严重程度。"""
        baseline = [10.0] * 15
        result = self.detector.detect(baseline, 200.0)
        assert result.is_anomaly
        assert result.severity == "critical"
        assert result.confidence == 0.95

    def test_severity_high(self):
        """较大偏离应标记为 high 严重程度。"""
        baseline = [10.0] * 15
        result = self.detector.detect(baseline, 60.0)
        assert result.is_anomaly
        assert result.severity == "high"

    def test_severity_medium(self):
        """中等偏离应标记为 medium 严重程度。"""
        baseline = [10.0] * 15
        result = self.detector.detect(baseline, 35.0)
        assert result.is_anomaly
        assert result.severity == "medium"

    def test_anomaly_result_fields(self):
        """AnomalyResult 应包含所有必要字段。"""
        baseline = [10.0] * 15
        result = self.detector.detect(baseline, 100.0)
        assert hasattr(result, "is_anomaly")
        assert hasattr(result, "anomaly_type")
        assert hasattr(result, "severity")
        assert hasattr(result, "baseline_value")
        assert hasattr(result, "current_value")
        assert hasattr(result, "deviation_ratio")
        assert hasattr(result, "confidence")
        assert hasattr(result, "details")

    def test_detect_batch(self):
        """detect_batch 应返回与序列长度相同的结果列表。"""
        series = [10.0] * 10 + [100.0, 10.0, 10.0]
        results = self.detector.detect_batch(series)
        assert len(results) == len(series)

    def test_detect_dict_compatibility(self):
        """detect_dict 应返回 dict 格式结果。"""
        baseline = [10.0] * 15
        result = self.detector.detect_dict(baseline, 100.0)
        assert isinstance(result, dict)
        assert "is_anomaly" in result
        assert "score" in result
        assert "deviation" in result
        assert "bounds" in result
        assert "median" in result

    def test_deviation_threshold(self):
        """偏离比超过 deviation_threshold 应触发异常。"""
        baseline = [10.0] * 15
        # median=10, current=35 → deviation_ratio=3.5 > 3.0 threshold
        result = self.detector.detect(baseline, 35.0)
        assert result.is_anomaly
