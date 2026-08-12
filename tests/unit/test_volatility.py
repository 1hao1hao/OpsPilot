"""波动突变检测算法单元测试。PRD-04 §3.2。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from deeprca.detection.volatility import VolatilityDetector


class TestVolatilityDetector:
    """波动性突变检测测试。"""

    def setup_method(self):
        self.detector = VolatilityDetector()

    def test_stable_series_no_spike(self):
        """稳定序列不应检测到突变。"""
        series = [10.0] * 20
        results = self.detector.detect(series)
        spikes = [r for r in results if r["is_spike"]]
        assert len(spikes) == 0

    def test_sudden_spike_detected(self):
        """突然放大的波动应检测到突变。"""
        series = [10.0] * 18 + [100.0, 10.0, 10.0, 10.0]
        results = self.detector.detect(series)
        spikes = [r for r in results if r["is_spike"]]
        assert len(spikes) > 0

    def test_empty_series(self):
        """空序列应安全返回。"""
        results = self.detector.detect([])
        assert len(results) == 0

    def test_short_series(self):
        """过短序列应安全返回。"""
        results = self.detector.detect([1.0, 2.0, 3.0])
        assert len(results) == 0

    def test_result_fields(self):
        """结果应包含必要字段。"""
        series = [10.0] * 18 + [100.0, 10.0, 10.0, 10.0]
        results = self.detector.detect(series)
        if results:
            r = results[0]
            assert "is_spike" in r
            assert "index" in r
            assert "value" in r
            assert "volatility" in r

    def test_custom_window_size(self):
        """自定义窗口大小应正常工作。"""
        detector = VolatilityDetector(window_size=5)
        series = [10.0] * 10 + [100.0, 10.0, 10.0, 10.0, 10.0]
        results = detector.detect(series)
        assert isinstance(results, list)

    def test_detect_volatility_change_stable(self):
        """稳定序列的波动性变化应为 False。"""
        series = [10.0] * 30
        result = self.detector.detect_volatility_change(series)
        assert not result["has_volatility_change"]

    def test_detect_volatility_change_spike(self):
        """含突变的序列应检测到波动性变化。"""
        series = [10.0] * 35 + [100.0, 200.0, 50.0, 150.0, 80.0] * 4
        result = self.detector.detect_volatility_change(series)
        assert result["has_volatility_change"]
        assert "baseline_volatility" in result
        assert "current_volatility" in result
        assert "change_ratio" in result

    def test_detect_volatility_change_insufficient(self):
        """数据不足时应安全返回。"""
        result = self.detector.detect_volatility_change([1.0, 2.0])
        assert not result["has_volatility_change"]
