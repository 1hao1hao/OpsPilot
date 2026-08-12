"""波动检测器。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.3-3.4.4</td></tr>
<tr><td>0.2.0</td><td>修复: 基线排除当前窗口，避免尖峰膨胀全局 std 导致漏检</td><td>BUGFIX</td></tr>
<tr><td>0.3.0</td><td>PRD-04 对齐: 可配置参数、返回 dict 含 change_ratio</td><td>PRD-04 §3.2</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import numpy as np


class VolatilityDetector:
    """波动检测器。检测时序数据的波动异常。"""

    def __init__(
        self, window_size: int = 10, volatility_threshold: float = 3.0
    ) -> None:
        self.window_size = window_size
        self.volatility_threshold = volatility_threshold

    def detect(self, series: list[float], window: int | None = None) -> list[dict]:
        """检测时序中的波动突变点。

        算法:
        1. 对每个滑动窗口计算标准差
        2. 基线 = 排除当前窗口后剩余序列的标准差（避免尖峰膨胀基线）
        3. 窗口标准差 > threshold * 基线标准差 → 突变点

        Args:
            series: 时序数据
            window: 滑动窗口大小，默认使用 self.window_size

        Returns:
            [{"index": int, "value": float, "volatility": float, "is_spike": bool}]
        """
        w = window or self.window_size
        arr = np.array(series, dtype=float)
        n = arr.size

        if n < w * 2 or n == 0:
            return []

        results: list[dict] = []

        for i in range(n - w + 1):
            window_slice = arr[i : i + w]
            window_std = float(np.std(window_slice))

            # 基线: 排除当前窗口后的剩余序列
            mask = np.ones(n, dtype=bool)
            mask[i : i + w] = False
            baseline_data = arr[mask]
            baseline_std = float(np.std(baseline_data)) if baseline_data.size > 0 else 0.0

            if baseline_std == 0:
                is_spike = window_std > 0
            else:
                is_spike = window_std > self.volatility_threshold * baseline_std

            results.append(
                {
                    "index": i,
                    "value": round(float(arr[i]), 4),
                    "volatility": round(window_std, 4),
                    "is_spike": is_spike,
                }
            )

        return results

    def detect_volatility_change(self, series: list[float]) -> dict:
        """检测波动性突变（PRD-04 §3.2 格式）。

        对比最近波动与基线波动。

        Returns:
            {
                "has_volatility_change": bool,
                "baseline_volatility": float,
                "current_volatility": float,
                "change_ratio": float,
            }
        """
        if len(series) < self.window_size * 2:
            return {"has_volatility_change": False, "reason": "数据不足"}

        # 计算滚动标准差
        volatilities: list[float] = []
        for i in range(self.window_size, len(series)):
            window = series[i - self.window_size : i]
            volatilities.append(float(np.std(window)))

        recent_vol = float(np.mean(volatilities[-self.window_size:]))
        baseline_vol = float(np.mean(volatilities[: -self.window_size])) if len(volatilities) > self.window_size else 0.0

        if baseline_vol == 0:
            change_ratio = float("inf") if recent_vol > 0 else 1.0
        else:
            change_ratio = recent_vol / baseline_vol

        has_change = change_ratio > self.volatility_threshold

        return {
            "has_volatility_change": has_change,
            "baseline_volatility": round(baseline_vol, 4),
            "current_volatility": round(recent_vol, 4),
            "change_ratio": round(change_ratio, 4),
        }
