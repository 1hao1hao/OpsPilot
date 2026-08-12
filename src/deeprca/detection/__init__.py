"""异常检测算法模块。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.3-3.4.4</td></tr>
<tr><td>0.2.0</td><td>PRD-04 对齐: AnomalyResult 数据类、可配置参数、detect_batch</td><td>PRD-04 §3</td></tr>
</table>
@author xianhuimeng
"""

from deeprca.detection.quantile import QuantileAnomalyDetector, AnomalyResult
from deeprca.detection.volatility import VolatilityDetector
from deeprca.detection.comparator import MultiDimensionComparator
from deeprca.detection.filters import MetricFilter, NoiseFilter, ExpertRuleEngine

__all__ = [
    "QuantileAnomalyDetector",
    "AnomalyResult",
    "VolatilityDetector",
    "MultiDimensionComparator",
    "MetricFilter",
    "NoiseFilter",
    "ExpertRuleEngine",
]
