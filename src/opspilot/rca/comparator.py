"""多维度对比器。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.3-3.4.4</td></tr>
<tr><td>0.2.0</td><td>PRD-04 对齐: verdict/confidence 字段、列表输入支持</td><td>PRD-04 §3.3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import numpy as np


class MultiDimensionComparator:
    """多维度对比器。周同比、日环比。"""

    # PRD-04 §3.3: 显著性阈值
    WOW_THRESHOLD: float = 0.3  # 30%
    DOD_THRESHOLD: float = 0.2  # 20%

    def compare_week_over_week(self, current: float, last_week: float) -> dict:
        """周同比: (current - last_week) / last_week * 100%"""
        if last_week == 0:
            change_pct = 0.0 if current == 0 else float("inf")
        else:
            change_pct = (current - last_week) / last_week * 100.0

        is_significant = abs(change_pct) > 30.0

        return {
            "week_change": round(change_pct, 2),
            "is_significant": is_significant,
        }

    def compare_day_over_day(self, current: float, yesterday: float) -> dict:
        """日环比: (current - yesterday) / yesterday * 100%"""
        if yesterday == 0:
            change_pct = 0.0 if current == 0 else float("inf")
        else:
            change_pct = (current - yesterday) / yesterday * 100.0

        is_significant = abs(change_pct) > 30.0

        return {
            "day_change": round(change_pct, 2),
            "is_significant": is_significant,
        }

    def compare(self, current: float, baselines: dict) -> dict:
        """综合对比（标量输入）。

        Args:
            current: 当前值
            baselines: {"last_week": float, "yesterday": float}

        Returns:
            {"week_change": float, "day_change": float, "is_significant": bool,
             "is_anomaly": bool}
        """
        last_week = baselines.get("last_week")
        yesterday = baselines.get("yesterday")

        if last_week is None:
            week_result = {"week_change": None, "is_significant": False}
        else:
            week_result = self.compare_week_over_week(current, float(last_week))

        if yesterday is None:
            day_result = {"day_change": None, "is_significant": False}
        else:
            day_result = self.compare_day_over_day(current, float(yesterday))

        is_significant = week_result["is_significant"] or day_result["is_significant"]

        return {
            "week_change": week_result["week_change"],
            "day_change": day_result["day_change"],
            "is_significant": is_significant,
            "is_anomaly": is_significant,
        }

    def compare_series(
        self,
        current_series: list[float],
        week_ago_series: list[float],
        day_ago_series: list[float],
    ) -> dict:
        """PRD-04 §3.3: 序列对比。

        对当前序列、一周前序列、一天前序列做周同比和日环比。

        Returns:
            {
                "week_over_week": {current_avg, baseline_avg, change_ratio, is_significant},
                "day_over_day": {current_avg, baseline_avg, change_ratio, is_significant},
                "verdict": "significant_anomaly" | "moderate_anomaly" | "normal",
                "confidence": float,
            }
        """
        current_avg = float(np.mean(current_series)) if current_series else 0.0
        week_avg = float(np.mean(week_ago_series)) if week_ago_series else 0.0
        day_avg = float(np.mean(day_ago_series)) if day_ago_series else 0.0

        # 周同比
        wow_ratio = current_avg / week_avg if week_avg != 0 else (
            float("inf") if current_avg > 0 else 1.0
        )
        wow_significant = abs(wow_ratio - 1.0) > self.WOW_THRESHOLD

        # 日环比
        dod_ratio = current_avg / day_avg if day_avg != 0 else (
            float("inf") if current_avg > 0 else 1.0
        )
        dod_significant = abs(dod_ratio - 1.0) > self.DOD_THRESHOLD

        # 双重确认
        if wow_significant and dod_significant:
            verdict = "significant_anomaly"
            confidence = 0.85
        elif wow_significant or dod_significant:
            verdict = "moderate_anomaly"
            confidence = 0.6
        else:
            verdict = "normal"
            confidence = 0.3

        return {
            "week_over_week": {
                "current_avg": round(current_avg, 4),
                "baseline_avg": round(week_avg, 4),
                "change_ratio": round(wow_ratio, 4) if wow_ratio != float("inf") else float("inf"),
                "is_significant": wow_significant,
            },
            "day_over_day": {
                "current_avg": round(current_avg, 4),
                "baseline_avg": round(day_avg, 4),
                "change_ratio": round(dod_ratio, 4) if dod_ratio != float("inf") else float("inf"),
                "is_significant": dod_significant,
            },
            "verdict": verdict,
            "confidence": confidence,
        }
