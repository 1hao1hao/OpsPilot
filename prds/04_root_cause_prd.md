# DeepRCA-Agent 根因定位 Agent PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-10 |
| 状态 | Draft |
| 负责人 | - |
| 关联文档 | 01_overview_prd.md, 02_general_analyzer_prd.md, 03_domain_expert_prd.md |

## 1. 概述

根因定位 Agent 是 DeepRCA-Agent 系统的第三层 Agent，接收所有子 Agent 的分析结果和证据池，结合多维指标筛选、多维度对比、异常检测算法和专家经验规则，执行最终根因推理，输出根因结论、置信度和证据链。

本文档定义根因定位 Agent 的核心算法（四分位+波动异常检测）、多维指标筛选策略、专家经验规则库、LLM 推理逻辑和输出结构。

## 2. 根因定位流程

```
证据池（来自 Collector）
  │
  ▼
┌─────────────────────────────┐
│  Step 1: 多维指标筛选        │
│  高QPS + 高失败率 + TP99突变 │
│  → 筛选出关键异常指标        │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  Step 2: 多维度对比          │
│  周同比 + 日环比             │
│  → 过滤低影响抖动            │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  Step 3: 异常检测算法        │
│  四分位 + 波动检测           │
│  → 确认异常的真实性          │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  Step 4: 证据关联排序        │
│  按置信度×权重排序           │
│  → 提取 Top-N 证据          │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  Step 5: 专家经验匹配        │
│  规则库 + 历史故障匹配       │
│  → 增强或修正推理结论        │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  Step 6: LLM 根因推理        │
│  融合所有信息生成根因结论    │
│  → 输出根因 + 置信度 + 证据链│
└─────────────────────────────┘
```

## 3. 异常检测算法

### 3.1 四分位+波动算法

核心设计决策：使用确定性统计学算法替代大模型做异常检测，消除幻觉问题。

```python
import numpy as np
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class AnomalyResult:
    is_anomaly: bool
    anomaly_type: str          # spike / drop / level_shift / volatility
    severity: str              # low / medium / high / critical
    baseline_value: float
    current_value: float
    deviation_ratio: float     # 当前值 / 基线值
    confidence: float          # 0.0 ~ 1.0
    details: dict

class QuantileAnomalyDetector:
    """四分位+波动异常检测器"""

    def __init__(
        self,
        iqr_multiplier: float = 1.5,      # 四分位距倍数
        deviation_threshold: float = 3.0,  # 偏离倍数阈值
        window_size: int = 30,             # 基线窗口大小
        min_points: int = 10,              # 最小数据点数
    ):
        self.iqr_multiplier = iqr_multiplier
        self.deviation_threshold = deviation_threshold
        self.window_size = window_size
        self.min_points = min_points

    def detect(self, data_points: List[float], current_value: float) -> AnomalyResult:
        """检测当前值是否异常

        Args:
            data_points: 历史数据点（基线）
            current_value: 当前待检测值
        """
        if len(data_points) < self.min_points:
            return AnomalyResult(
                is_anomaly=False,
                anomaly_type="insufficient_data",
                severity="low",
                baseline_value=0,
                current_value=current_value,
                deviation_ratio=1.0,
                confidence=0.0,
                details={"reason": "数据点不足"},
            )

        arr = np.array(data_points)

        # Step 1: 计算四分位
        q1 = np.percentile(arr, 25)
        q3 = np.percentile(arr, 75)
        iqr = q3 - q1
        median = np.median(arr)

        # 正常范围 [q1 - 1.5*IQR, q3 + 1.5*IQR]
        lower_bound = q1 - self.iqr_multiplier * iqr
        upper_bound = q3 + self.iqr_multiplier * iqr

        # Step 2: 计算偏离比
        if median != 0:
            deviation_ratio = current_value / median
        else:
            deviation_ratio = float('inf') if current_value > 0 else 1.0

        # Step 3: 判断异常
        is_anomaly = (
            current_value > upper_bound or
            current_value < lower_bound or
            deviation_ratio > self.deviation_threshold or
            deviation_ratio < (1.0 / self.deviation_threshold)
        )

        if not is_anomaly:
            return AnomalyResult(
                is_anomaly=False,
                anomaly_type="normal",
                severity="low",
                baseline_value=median,
                current_value=current_value,
                deviation_ratio=deviation_ratio,
                confidence=0.3,
                details={"q1": q1, "q3": q3, "iqr": iqr, "bounds": [lower_bound, upper_bound]},
            )

        # Step 4: 确定异常类型
        if current_value > upper_bound:
            anomaly_type = "spike"
        elif current_value < lower_bound:
            anomaly_type = "drop"
        else:
            anomaly_type = "level_shift"

        # Step 5: 确定严重程度
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
            current_value=current_value,
            deviation_ratio=deviation_ratio,
            confidence=confidence,
            details={
                "q1": q1, "q3": q3, "iqr": iqr,
                "lower_bound": lower_bound, "upper_bound": upper_bound,
            },
        )

    def detect_batch(self, series: List[float]) -> List[AnomalyResult]:
        """批量检测时序数据中的异常点"""
        results = []
        for i in range(len(series)):
            start = max(0, i - self.window_size)
            baseline = series[start:i]
            results.append(self.detect(baseline, series[i]))
        return results
```

### 3.2 波动检测算法

```python
class VolatilityDetector:
    """波动检测器 - 检测数据波动性突变"""

    def __init__(self, window_size: int = 10, volatility_threshold: float = 3.0):
        self.window_size = window_size
        self.volatility_threshold = volatility_threshold

    def detect(self, series: List[float]) -> dict:
        """检测波动性突变

        Returns:
            {
                "has_volatility_change": True,
                "baseline_volatility": 0.5,
                "current_volatility": 3.2,
                "change_ratio": 6.4,
                "anomaly_points": [14, 15, 16],  # 异常点的索引
            }
        """
        if len(series) < self.window_size * 2:
            return {"has_volatility_change": False, "reason": "数据不足"}

        # 计算滚动标准差
        volatilities = []
        for i in range(self.window_size, len(series)):
            window = series[i - self.window_size:i]
            volatilities.append(np.std(window))

        # 对比最近波动与基线波动
        recent_vol = np.mean(volatilities[-self.window_size:])
        baseline_vol = np.mean(volatilities[:-self.window_size])

        if baseline_vol == 0:
            change_ratio = float('inf') if recent_vol > 0 else 1.0
        else:
            change_ratio = recent_vol / baseline_vol

        has_change = change_ratio > self.volatility_threshold

        return {
            "has_volatility_change": has_change,
            "baseline_volatility": baseline_vol,
            "current_volatility": recent_vol,
            "change_ratio": change_ratio,
        }
```

### 3.3 多维度对比算法

```python
class MultiDimensionComparator:
    """多维度对比器 - 周同比 + 日环比"""

    def compare(
        self,
        current_series: List[float],
        week_ago_series: List[float],
        day_ago_series: List[float],
    ) -> dict:
        """执行多维度对比

        Returns:
            {
                "week_over_week": {
                    "current_avg": 1200,
                    "baseline_avg": 800,
                    "change_ratio": 1.5,
                    "is_significant": True,
                },
                "day_over_day": {
                    "current_avg": 1200,
                    "baseline_avg": 950,
                    "change_ratio": 1.26,
                    "is_significant": True,
                },
                "verdict": "significant_anomaly",
                "confidence": 0.85,
            }
        """
        current_avg = np.mean(current_series)
        week_avg = np.mean(week_ago_series)
        day_avg = np.mean(day_ago_series)

        # 周同比
        wow_ratio = current_avg / week_avg if week_avg != 0 else float('inf')
        wow_significant = abs(wow_ratio - 1.0) > 0.3  # 30% 变化

        # 日环比
        dod_ratio = current_avg / day_avg if day_avg != 0 else float('inf')
        dod_significant = abs(dod_ratio - 1.0) > 0.2  # 20% 变化

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
                "current_avg": current_avg,
                "baseline_avg": week_avg,
                "change_ratio": wow_ratio,
                "is_significant": wow_significant,
            },
            "day_over_day": {
                "current_avg": current_avg,
                "baseline_avg": day_avg,
                "change_ratio": dod_ratio,
                "is_significant": dod_significant,
            },
            "verdict": verdict,
            "confidence": confidence,
        }
```

## 4. 多维指标筛选

### 4.1 指标筛选策略

```python
class MetricFilter:
    """多维指标筛选器"""

    # 筛选维度
    FILTER_DIMENSIONS = {
        "high_qps": {
            "description": "高 QPS 指标",
            "threshold": 1000,
            "metric": "qps",
            "weight": 1.0,
        },
        "high_error_rate": {
            "description": "高失败率指标",
            "threshold": 0.05,  # 5%
            "metric": "error_rate",
            "weight": 1.5,
        },
        "tp99_spike": {
            "description": "TP99 突变",
            "metric": "tp99",
            "anomaly_detector": True,  # 使用异常检测算法
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

    def filter(self, metrics_data: dict) -> List[dict]:
        """筛选关键异常指标

        Args:
            metrics_data: 所有指标数据

        Returns:
            筛选后的关键异常指标列表，按权重排序
        """
        results = []
        detector = QuantileAnomalyDetector()

        for dim_name, config in self.FILTER_DIMENSIONS.items():
            metric_names = config.get("metrics", [config.get("metric")])

            for metric_name in metric_names:
                data = metrics_data.get(metric_name)
                if not data or "data_points" not in data:
                    continue

                points = [p["value"] for p in data["data_points"]]
                current = points[-1] if points else 0

                if config.get("anomaly_detector"):
                    # 使用异常检测算法
                    anomaly = detector.detect(points[:-1], current)
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

        # 按置信度排序
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results
```

### 4.2 过滤低影响抖动

```python
class NoiseFilter:
    """低影响抖动过滤器"""

    def filter_noise(self, anomalies: List[dict]) -> List[dict]:
        """过滤低影响抖动，保留真正的异常"""
        filtered = []
        for anomaly in anomalies:
            # 规则 1: 偏离比 < 2 倍的视为低影响抖动
            if anomaly.get("deviation_ratio", 1.0) < 2.0 and anomaly.get("deviation_ratio", 1.0) > 0.5:
                continue

            # 规则 2: 持续时间 < 2 分钟的瞬态抖动过滤
            if anomaly.get("duration_seconds", 0) < 120:
                continue

            # 规则 3: 低置信度 + 低偏离比
            if anomaly.get("confidence", 0) < 0.5 and anomaly.get("deviation_ratio", 1.0) < 3.0:
                continue

            filtered.append(anomaly)

        return filtered
```

## 5. 专家经验规则库

```python
class ExpertRuleEngine:
    """专家经验规则引擎"""

    def __init__(self):
        self.rules = self._load_rules()

    def _load_rules(self) -> List[dict]:
        """加载专家经验规则"""
        return [
            {
                "rule_id": "R001",
                "name": "变更+故障时间吻合",
                "condition": {
                    "change_within_minutes": 30,
                    "alert_type": ["timeout", "error_rate"],
                },
                "action": "boost_confidence",
                "boost": 0.15,
                "description": "30分钟内有变更且告警类型为超时或错误率，提升根因为变更的置信度",
            },
            {
                "rule_id": "R002",
                "name": "DB主从延迟+读超时",
                "condition": {
                    "evidence_contains": ["slave_delay", "Lock wait timeout"],
                    "alert_type": "timeout",
                },
                "action": "set_root_cause",
                "root_cause_template": "数据库主从延迟导致读请求超时",
                "confidence": 0.9,
            },
            {
                "rule_id": "R003",
                "name": "OOM+服务重启",
                "condition": {
                    "evidence_contains": ["OutOfMemoryError"],
                    "evidence_contains_any": ["pod_restart", "OOMKilled"],
                },
                "action": "set_root_cause",
                "root_cause_template": "服务内存溢出导致 Pod 被 Kill 并重启",
                "confidence": 0.95,
            },
            {
                "rule_id": "R004",
                "name": "消费积压+消费者离线",
                "condition": {
                    "evidence_contains": ["consumer_offline"],
                    "evidence_contains_any": ["consumer_lag", "rebalance"],
                },
                "action": "set_root_cause",
                "root_cause_template": "Kafka 消费者离线导致消息积压",
                "confidence": 0.9,
            },
            {
                "rule_id": "R005",
                "name": "流量突增+资源饱和",
                "condition": {
                    "metric_anomaly": ["qps_spike"],
                    "resource_threshold": 0.85,
                },
                "action": "set_root_cause",
                "root_cause_template": "上游流量突增导致服务资源饱和",
                "confidence": 0.85,
            },
            {
                "rule_id": "R006",
                "name": "熔断触发+下游异常",
                "condition": {
                    "evidence_contains": ["circuit_breaker"],
                    "downstream_anomaly": True,
                },
                "action": "boost_confidence",
                "boost": 0.1,
                "description": "熔断器触发且下游存在异常，提升下游异常的根因置信度",
            },
            {
                "rule_id": "R007",
                "name": "配置变更+连接池异常",
                "condition": {
                    "change_type": "config",
                    "evidence_contains_any": ["connection_pool", "active_connections"],
                },
                "action": "set_root_cause",
                "root_cause_template": "配置变更导致连接池参数异常",
                "confidence": 0.85,
            },
            {
                "rule_id": "R008",
                "name": "多维度共振",
                "condition": {
                    "anomaly_dimensions_count": 3,  # 3 个以上维度同时异常
                },
                "action": "boost_confidence",
                "boost": 0.1,
                "description": "3个以上维度同时异常，提升整体根因置信度",
            },
        ]

    def evaluate(
        self,
        evidence_pool: dict,
        sub_agent_results: List[dict],
        alert: dict,
        metric_anomalies: List[dict],
    ) -> List[dict]:
        """评估所有规则，返回匹配结果"""
        matched_rules = []

        # 提取所有证据文本
        all_evidence_text = " ".join([
            ev.get("content", "") for ev in evidence_pool.get("sorted_evidences", [])
        ])

        for rule in self.rules:
            condition = rule["condition"]
            matched = True

            # 检查变更时间窗口
            if "change_within_minutes" in condition:
                change_evidences = [
                    ev for ev in evidence_pool.get("sorted_evidences", [])
                    if ev.get("dimension") == "change"
                ]
                if not change_evidences:
                    matched = False
                # 检查是否有在指定时间窗口内的变更
                # (简化: 如果有变更证据就认为可能匹配)

            # 检查证据包含
            if "evidence_contains" in condition:
                for keyword in condition["evidence_contains"]:
                    if keyword.lower() not in all_evidence_text.lower():
                        matched = False
                        break

            # 检查证据包含任一
            if "evidence_contains_any" in condition:
                if not any(kw.lower() in all_evidence_text.lower() for kw in condition["evidence_contains_any"]):
                    matched = False

            # 检查告警类型
            if "alert_type" in condition:
                if alert.get("alert_type") not in condition["alert_type"]:
                    matched = False

            # 检查异常维度数量
            if "anomaly_dimensions_count" in condition:
                anomaly_dims = set(a.get("dimension") for a in metric_anomalies)
                if len(anomaly_dims) < condition["anomaly_dimensions_count"]:
                    matched = False

            if matched:
                matched_rules.append(rule)

        return matched_rules
```

## 6. 根因定位 Agent

### 6.1 Agent 定义

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

class RootCauseAgent:
    """根因定位 Agent"""

    def __init__(self, model_name: str = "gpt-4o"):
        self.llm = ChatOpenAI(model=model_name, temperature=0.1)
        self.detector = QuantileAnomalyDetector()
        self.comparator = MultiDimensionComparator()
        self.metric_filter = MetricFilter()
        self.noise_filter = NoiseFilter()
        self.rule_engine = ExpertRuleEngine()

    async def analyze(
        self,
        alert: dict,
        evidence: dict,
        sub_agent_results: List[dict],
    ) -> dict:
        """执行根因定位"""

        # Step 1: 多维指标筛选
        metric_anomalies = self.metric_filter.filter(
            evidence.get("metrics_data", {})
        )

        # Step 2: 过滤低影响抖动
        metric_anomalies = self.noise_filter.filter_noise(metric_anomalies)

        # Step 3: 专家经验规则匹配
        matched_rules = self.rule_engine.evaluate(
            evidence_pool=evidence,
            sub_agent_results=sub_agent_results,
            alert=alert,
            metric_anomalies=metric_anomalies,
        )

        # Step 4: 检查是否有确定性规则直接定位根因
        for rule in matched_rules:
            if rule.get("action") == "set_root_cause":
                return self._build_result(
                    conclusion=rule["root_cause_template"],
                    confidence=rule["confidence"],
                    evidence_chain=self._extract_evidence_chain(evidence, sub_agent_results),
                    metric_anomalies=metric_anomalies,
                    matched_rules=matched_rules,
                    rule_driven=True,
                )

        # Step 5: LLM 推理（无确定性规则匹配时）
        llm_result = await self._llm_reasoning(
            alert, evidence, sub_agent_results, metric_anomalies, matched_rules
        )

        return self._build_result(
            conclusion=llm_result["conclusion"],
            confidence=llm_result["confidence"],
            evidence_chain=self._extract_evidence_chain(evidence, sub_agent_results),
            metric_anomalies=metric_anomalies,
            matched_rules=matched_rules,
            rule_driven=False,
            suggestions=llm_result.get("suggestions", []),
        )

    async def _llm_reasoning(
        self,
        alert: dict,
        evidence: dict,
        sub_agent_results: List[dict],
        metric_anomalies: List[dict],
        matched_rules: List[dict],
    ) -> dict:
        """使用 LLM 进行根因推理"""

        prompt = ChatPromptTemplate.from_messages([
            ("system", ROOT_CAUSE_SYSTEM_PROMPT),
            ("human", """
基于以下信息进行根因定位：

## 告警信息
{alert}

## 子 Agent 分析结果
{sub_agent_results}

## 证据池摘要
{evidence_summary}

## 异常指标
{metric_anomalies}

## 匹配的专家规则
{matched_rules}

请分析并输出：
1. 根因结论
2. 置信度 (0.0~1.0)
3. 证据链（按重要性排序）
4. 建议措施
"""),
        ])

        chain = prompt | self.llm
        response = await chain.ainvoke({
            "alert": json.dumps(alert, ensure_ascii=False),
            "sub_agent_results": json.dumps(sub_agent_results, ensure_ascii=False, indent=2),
            "evidence_summary": json.dumps(evidence.get("summary", {}), ensure_ascii=False),
            "metric_anomalies": json.dumps(metric_anomalies, ensure_ascii=False),
            "matched_rules": json.dumps([{"rule_id": r["rule_id"], "name": r["name"]} for r in matched_rules], ensure_ascii=False),
        })

        return self._parse_llm_response(response.content)

    def _build_result(
        self,
        conclusion: str,
        confidence: float,
        evidence_chain: List[dict],
        metric_anomalies: List[dict],
        matched_rules: List[dict],
        rule_driven: bool,
        suggestions: List[str] = None,
    ) -> dict:
        """构建根因定位结果"""
        # 应用规则置信度加成
        for rule in matched_rules:
            if rule.get("action") == "boost_confidence":
                confidence = min(confidence + rule.get("boost", 0), 0.95)

        return {
            "conclusion": conclusion,
            "confidence": round(confidence, 2),
            "category": self._categorize(conclusion, evidence_chain),
            "evidence_chain": evidence_chain[:5],  # Top 5 证据
            "metric_anomalies": metric_anomalies[:3],  # Top 3 异常指标
            "matched_rules": [{"rule_id": r["rule_id"], "name": r["name"]} for r in matched_rules],
            "rule_driven": rule_driven,
            "suggestions": suggestions or self._generate_suggestions(conclusion, evidence_chain),
        }
```

### 6.2 System Prompt

```python
ROOT_CAUSE_SYSTEM_PROMPT = """你是故障根因定位专家 Agent。

你的任务是基于多维度分析结果和证据池，推理出故障的根本原因。

分析原则：
1. 优先采用确定性结论：如果专家规则已匹配，直接使用规则结论
2. 变更优先：90% 的故障由变更引起，优先排查变更维度
3. 证据驱动：每个结论必须有证据支撑，不得臆测
4. 置信度量化：基于证据数量和质量计算置信度
   - 5+ 高置信度证据: 0.9~0.95
   - 3~4 中等置信度证据: 0.7~0.85
   - 1~2 低置信度证据: 0.5~0.7
   - 仅推测无实证: < 0.5
5. 证据链排序：按置信度从高到低排序，最多保留 5 条

输出格式（JSON）：
{
  "conclusion": "根因结论（一句话）",
  "confidence": 0.85,
  "evidence_chain": [
    {"dimension": "change", "evidence": "...", "confidence": 0.9},
    ...
  ],
  "suggestions": ["建议措施1", "建议措施2", ...]
}

常见根因模式：
- 变更导致: 配置变更/代码部署/扩缩容 → 服务行为变化
- 下游异常: DB/Redis/MQ 异常 → 调用超时/失败
- 资源饱和: CPU/内存/磁盘饱和 → 性能退化
- 流量突增: 上游 QPS 突增 → 资源耗尽
- 已知问题: 历史故障重现 → 快速定位
"""
```

## 7. 证据链构建

```python
def _extract_evidence_chain(
    self,
    evidence: dict,
    sub_agent_results: List[dict],
) -> List[dict]:
    """提取并排序证据链"""
    chain = []

    # 从证据池提取
    for ev in evidence.get("sorted_evidences", []):
        chain.append({
            "dimension": ev.get("dimension"),
            "evidence": ev.get("content"),
            "confidence": ev.get("confidence"),
            "source": ev.get("source"),
            "level": ev.get("level"),
        })

    # 从子 Agent 结果补充
    for result in sub_agent_results:
        for finding in result.get("findings", []):
            chain.append({
                "dimension": result.get("dimension"),
                "evidence": finding.get("description"),
                "confidence": finding.get("confidence"),
                "source": result.get("agent_name"),
                "category": finding.get("category"),
            })

    # 去重 + 排序
    seen = set()
    unique_chain = []
    for item in chain:
        key = item["evidence"][:50]  # 前 50 字符去重
        if key not in seen:
            seen.add(key)
            unique_chain.append(item)

    unique_chain.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return unique_chain
```

## 8. 建议措施生成

```python
SUGGESTION_TEMPLATES = {
    "change": [
        "回滚变更: {change_description}",
        "检查变更影响范围，评估是否需要紧急回滚",
    ],
    "db_slave_delay": [
        "检查 DB 主从同步状态: SHOW SLAVE STATUS",
        "评估是否需要重建从库",
        "临时切流量到健康的从库",
    ],
    "db_lock": [
        "检查锁等待: SELECT * FROM information_schema.innodb_lock_waits",
        "Kill 长事务: KILL <connection_id>",
        "检查是否有未提交的事务",
    ],
    "redis_memory": [
        "检查大 Key: redis-cli --bigkeys",
        "检查热点 Key: redis-cli --hotkeys",
        "评估是否需要扩容 Redis 内存",
    ],
    "kafka_lag": [
        "检查消费者状态: 确认消费者是否在线",
        "临时增加消费者实例: 扩容消费者 Pod",
        "检查是否有 Rebalance 风暴",
    ],
    "rpc_circuit_breaker": [
        "检查熔断器状态和触发原因",
        "评估是否需要调整熔断阈值",
        "检查下游依赖服务健康状态",
    ],
    "resource_saturation": [
        "紧急扩容: 增加 Pod 副本数",
        "检查资源 Limit 配置是否合理",
        "清理不必要的资源占用",
    ],
    "oom": [
        "检查内存泄漏: 分析 Heap Dump",
        "临时重启服务恢复",
        "调整 JVM 内存参数 -Xmx",
    ],
}

def _generate_suggestions(self, conclusion: str, evidence_chain: List[dict]) -> List[str]:
    """根据根因结论和证据生成建议措施"""
    suggestions = []

    for evidence in evidence_chain:
        category = evidence.get("category", "")
        if category in SUGGESTION_TEMPLATES:
            suggestions.extend(SUGGESTION_TEMPLATES[category])

    # 去重
    return list(dict.fromkeys(suggestions))[:5]
```

## 9. 性能与准确性

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 根因定位延迟 | ≤ 10s | 从收到证据池到输出根因 |
| 规则匹配延迟 | ≤ 100ms | 专家规则引擎评估 |
| 异常检测延迟 | ≤ 500ms | 四分位算法执行 |
| LLM 推理延迟 | ≤ 8s | 无规则匹配时的 LLM 推理 |
| 根因命中率 | ≥ 50% | 与人工确认根因一致 |
| 关键线索命中率 | ≥ 75% | 关键线索在证据链 Top-5 |

## 10. 测试要点

| 测试场景 | 输入 | 期望输出 |
|----------|------|----------|
| DB 主从延迟超时 | slave_delay=15s + timeout 告警 | 规则 R002 匹配，置信度 ≥ 0.9 |
| OOM 重启 | OOM log + pod_restart | 规则 R003 匹配，置信度 ≥ 0.95 |
| 消费者离线积压 | consumer_offline + lag | 规则 R004 匹配，置信度 ≥ 0.9 |
| 变更 20 分钟内超时 | change + timeout | 规则 R001 加成，置信度提升 0.15 |
| 多维度共振 | 4 维度异常 | 规则 R008 加成，置信度提升 0.1 |
| 无规则匹配 | 复杂场景 | LLM 推理，置信度 0.6~0.85 |
| 低影响抖动过滤 | deviation_ratio=1.5 | 被过滤，不进入根因分析 |
| 数据不足降级 | < 10 个数据点 | 标记 insufficient_data，降级推理 |
