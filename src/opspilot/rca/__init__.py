"""Compatibility exports for DeepRCA's proven deterministic RCA algorithms.

Stage 1 keeps one implementation while moving callers to the OpsPilot
namespace. The implementations can be moved physically after the legacy API
compatibility layer is removed.
"""

from deeprca.detection.comparator import MultiDimensionComparator
from deeprca.detection.filters import ExpertRuleEngine, MetricFilter, NoiseFilter
from deeprca.detection.quantile import AnomalyResult, QuantileAnomalyDetector
from deeprca.detection.volatility import VolatilityDetector

__all__ = [
    "AnomalyResult",
    "ExpertRuleEngine",
    "MetricFilter",
    "MultiDimensionComparator",
    "NoiseFilter",
    "QuantileAnomalyDetector",
    "VolatilityDetector",
]

