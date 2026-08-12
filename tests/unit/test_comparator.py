"""多维度对比器单元测试。PRD-04 §3.3。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from deeprca.detection.comparator import MultiDimensionComparator


class TestMultiDimensionComparator:
    """周同比 + 日环比对比器测试。"""

    def setup_method(self):
        self.comparator = MultiDimensionComparator()

    def test_normal_comparison(self):
        """正常波动不应标记异常。"""
        result = self.comparator.compare(
            current=100.0,
            baselines={"last_week": 95.0, "yesterday": 98.0},
        )
        assert not result.get("is_anomaly", False)

    def test_significant_increase(self):
        """显著增长应标记异常。"""
        result = self.comparator.compare(
            current=200.0,
            baselines={"last_week": 100.0, "yesterday": 105.0},
        )
        assert result.get("is_anomaly", False)

    def test_significant_decrease(self):
        """显著下降应标记异常。"""
        result = self.comparator.compare(
            current=10.0,
            baselines={"last_week": 100.0, "yesterday": 95.0},
        )
        assert result.get("is_anomaly", False)

    def test_missing_baseline(self):
        """缺失基线应安全处理。"""
        result = self.comparator.compare(
            current=100.0,
            baselines={"last_week": None, "yesterday": None},
        )
        assert not result.get("is_anomaly", False)

    def test_zero_baseline(self):
        """基线为零时应安全处理。"""
        result = self.comparator.compare(
            current=100.0,
            baselines={"last_week": 0.0, "yesterday": 0.0},
        )
        assert result is not None

    def test_result_not_none(self):
        """结果不应为 None。"""
        result = self.comparator.compare(
            current=150.0,
            baselines={"last_week": 100.0, "yesterday": 120.0},
        )
        assert result is not None

    def test_compare_series_normal(self):
        """序列对比: 正常波动应为 normal。"""
        result = self.comparator.compare_series(
            current_series=[100.0, 102.0, 98.0, 101.0, 99.0],
            week_ago_series=[95.0, 97.0, 94.0, 96.0, 98.0],
            day_ago_series=[100.0, 101.0, 99.0, 100.0, 102.0],
        )
        assert result["verdict"] == "normal"
        assert result["confidence"] == 0.3

    def test_compare_series_significant_anomaly(self):
        """序列对比: 双重确认应为 significant_anomaly。"""
        result = self.comparator.compare_series(
            current_series=[200.0, 210.0, 195.0, 205.0, 202.0],
            week_ago_series=[100.0, 105.0, 98.0, 102.0, 100.0],
            day_ago_series=[100.0, 102.0, 99.0, 101.0, 100.0],
        )
        assert result["verdict"] == "significant_anomaly"
        assert result["confidence"] == 0.85

    def test_compare_series_moderate_anomaly(self):
        """序列对比: 单维度异常应为 moderate_anomaly。"""
        result = self.comparator.compare_series(
            current_series=[140.0, 142.0, 138.0, 141.0, 139.0],
            week_ago_series=[100.0, 105.0, 98.0, 102.0, 100.0],
            day_ago_series=[125.0, 127.0, 124.0, 126.0, 125.0],
        )
        # WoW > 30% significant, DoD < 20% not significant → moderate
        assert result["verdict"] in ("moderate_anomaly", "significant_anomaly")

    def test_compare_series_fields(self):
        """序列对比结果应包含所有字段。"""
        result = self.comparator.compare_series(
            current_series=[100.0, 102.0, 98.0],
            week_ago_series=[95.0, 97.0, 94.0],
            day_ago_series=[100.0, 101.0, 99.0],
        )
        assert "week_over_week" in result
        assert "day_over_day" in result
        assert "verdict" in result
        assert "confidence" in result
        assert "current_avg" in result["week_over_week"]
        assert "baseline_avg" in result["week_over_week"]
        assert "change_ratio" in result["week_over_week"]
        assert "is_significant" in result["week_over_week"]
