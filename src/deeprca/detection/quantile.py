"""四分位 IQR 异常检测器。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.3-3.4.4</td></tr>
<tr><td>0.2.0</td><td>PRD-04 对齐: AnomalyResult 数据类、可配置参数、severity/类型、detect_batch</td><td>PRD-04 §3.1</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class AnomalyResult:
    """异常检测结果。"""

    is_anomaly: bool
    anomaly_type: str  # spike / drop / level_shift / volatility / normal / insufficient_data
    severity: str  # low / medium / high / critical
    baseline_value: float
    current_value: float
    deviation_ratio: float
    confidence: float  # 0.0 ~ 1.0
    details: dict = field(default_factory=dict)


class QuantileAnomalyDetector:
    """四分位 IQR 异常检测器。确定性算法，不依赖 LLM。"""

    def __init__(
        self,
        iqr_multiplier: float = 1.5,
        deviation_threshold: float = 3.0,
        window_size: int = 30,
        min_points: int = 10,
    ) -> None:
        self.iqr_multiplier = iqr_multiplier
        self.deviation_threshold = deviation_threshold
        self.window_size = window_size
        self.min_points = min_points

    def detect(self, baseline: list[float], current: float) -> AnomalyResult:
        """检测当前值是否异常。

        算法:
        1. 检查数据点数 ≥ min_points，否则 insufficient_data
        2. Q1=25th, Q3=75th, IQR=Q3-Q1
        3. 正常范围 [Q1 - 1.5*IQR, Q3 + 1.5*IQR]
        4. deviation_ratio = current / median
        5. is_anomaly = 超出 IQR 边界 OR deviation_ratio 超阈值
        6. 确定 anomaly_type (spike/drop/level_shift) 和 severity (low~critical)

        Returns:
            AnomalyResult
        """
        if len(baseline) < self.min_points:
            return AnomalyResult(
                is_anomaly=False,
                anomaly_type="insufficient_data",
                severity="low",
                baseline_value=0.0,
                current_value=current,
                deviation_ratio=1.0,
                confidence=0.0,
                details={"reason": "数据点不足", "data_points": len(baseline)},
            )

        arr = np.array(baseline, dtype=float)

        q1 = float(np.percentile(arr, 25))
        q3 = float(np.percentile(arr, 75))
        iqr = q3 - q1
        median = float(np.median(arr))

        lower_bound = q1 - self.iqr_multiplier * iqr
        upper_bound = q3 + self.iqr_multiplier * iqr

        # 偏离比率
        if median != 0:
            deviation_ratio = abs(current / median)
        else:
            deviation_ratio = 0.0 if current == 0 else float("inf")

        # 异常判定
        is_anomaly = (
            current > upper_bound
            or current < lower_bound
            or deviation_ratio > self.deviation_threshold
            or (median != 0 and deviation_ratio < (1.0 / self.deviation_threshold))
        )

        if not is_anomaly:
            return AnomalyResult(
                is_anomaly=False,
                anomaly_type="normal",
                severity="low",
                baseline_value=median,
                current_value=current,
                deviation_ratio=deviation_ratio,
                confidence=0.3,
                details={
                    "q1": q1, "q3": q3, "iqr": iqr,
                    "lower_bound": lower_bound, "upper_bound": upper_bound,
                },
            )

        # 确定异常类型
        if current > upper_bound:
            anomaly_type = "spike"
        elif current < lower_bound:
            anomaly_type = "drop"
        else:
            anomaly_type = "level_shift"

        # 确定严重程度
        if deviation_ratio > 10 or deviation_ratio < 0.1:
            severity = "critical"
            confidence = 0.95
        elif deviation_ratio > 5 or deviation_ratio < 0.2:
            severity = "high"
            confidence = 0.85
        elif deviation_ratio > 3 or deviation_ratio < 0.33:
            severity = "medium"
            confidence = 0.75
        else:
            severity = "low"
            confidence = 0.6

        return AnomalyResult(
            is_anomaly=True,
            anomaly_type=anomaly_type,
            severity=severity,
            baseline_value=median,
            current_value=current,
            deviation_ratio=deviation_ratio,
            confidence=confidence,
            details={
                "q1": q1, "q3": q3, "iqr": iqr,
                "lower_bound": lower_bound, "upper_bound": upper_bound,
            },
        )

    def detect_batch(self, series: list[float]) -> list[AnomalyResult]:
        """批量检测时序数据中的异常点。"""
        results: list[AnomalyResult] = []
        for i in range(len(series)):
            start = max(0, i - self.window_size)
            baseline = series[start:i]
            results.append(self.detect(baseline, series[i]))
        return results

    # ------------------------------------------------------------------ #
    #  兼容方法：供 root_cause.py 原有调用方式使用
    # ------------------------------------------------------------------ #

    def detect_dict(self, baseline: list[float], current: float) -> dict:
        """返回 dict 格式（兼容旧调用）。"""
        result = self.detect(baseline, current)
        return {
            "is_anomaly": result.is_anomaly,
            "anomaly_type": result.anomaly_type,
            "severity": result.severity,
            "baseline_value": result.baseline_value,
            "current_value": result.current_value,
            "deviation_ratio": result.deviation_ratio,
            "confidence": result.confidence,
            "score": result.confidence,  # 兼容字段
            "deviation": result.deviation_ratio,  # 兼容字段
            "bounds": {
                "lower": result.details.get("lower_bound", 0.0),
                "upper": result.details.get("upper_bound", 0.0),
            },
            "median": result.baseline_value,
            "details": result.details,
        }
